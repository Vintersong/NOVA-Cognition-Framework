"""
nova_server_v2.py — NOVA v2 MCP Server

NOVA v2 adds three automation layers on top of v1:

AUTOMATION LAYER (new):
  - Confidence decay: shards that haven't been accessed sink in relevance
  - Auto-compaction: shards exceeding 30 turns get auto-summarized
  - Post-write enrichment hooks: local embeddings via sentence-transformers on create/update
  - Similarity merge suggestions: cosine > 0.85 flags candidate pairs
  - Usage tracking per operation: tool, shard IDs, timestamps

KNOWLEDGE GRAPH LAYER (new):
  - Inter-shard relationships: shard A influences shard B
  - Entity types: Concept, Project, Decision, Person, Tool, Event
  - Relation types: influences, depends_on, contradicts, extends, references
  - Four new tools: nova_shard_forget, nova_shard_consolidate, nova_graph_query, nova_graph_relate

MEMORY LAYER (v1, unchanged):
  - 7 core tools + 4 new = 11 total tools
  - JSON filesystem storage, no database dependency
  - Fuzzy + cosine retrieval with difflib fallback, local embeddings via all-MiniLM-L6-v2

Architecture:
  User (executive function)
    --> selects shards
  Shard System (modular memory)
    --> loaded into context
  LLM Processor (stateless reasoning)
    --> synthesizes and updates
  Automation Layer (background maintenance)
    --> decay, compact, enrich, suggest
  Knowledge Graph (inter-shard navigation)
    --> relationships, entities, pattern queries

Tools (12 total):
  nova_shard_interact   — load shards into context
  nova_shard_create     — create new shard (+ post-write hook)
  nova_shard_update     — append to shard (+ post-write hook + auto-compact)
  nova_shard_search     — search with confidence weighting
  nova_shard_list       — list all shards with confidence scores
  nova_shard_get        — read full raw shard content, no side effects
  nova_shard_merge      — merge shards into meta-shard
  nova_shard_archive    — soft-delete (sets intent=archived)
  nova_shard_forget     — NEW: hard soft-delete with provenance log
  nova_shard_consolidate — NEW: run decay + compact + merge suggestion cycle
  nova_graph_query      — NEW: query inter-shard knowledge graph
  nova_graph_relate     — NEW: manually add directed relation between shards
"""

import os
import re
import json
import math
import difflib
from datetime import datetime, timedelta
from typing import Optional
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ConfigDict
from filelock import FileLock
from nova_embeddings_local import enrich_shard, _generate_compaction_summary
from permissions import ToolPermissionContext
from models import UsageSummary
from session_store import SessionStore, NovaSession
from forgemaster_runtime import ForgemasterRuntime

# === Environment ===
# Paths default to repo root (one level up from mcp/) so the server works
# correctly regardless of the working directory it's launched from.
_REPO_ROOT = Path(__file__).parent.parent

SHARD_DIR = os.environ.get("NOVA_SHARD_DIR", str(_REPO_ROOT / "shards"))
INDEX_FILE = os.environ.get("NOVA_INDEX_FILE", str(_REPO_ROOT / "shard_index.json"))
GRAPH_FILE = os.environ.get("NOVA_GRAPH_FILE", str(_REPO_ROOT / "shard_graph.json"))
USAGE_LOG_FILE = os.environ.get("NOVA_USAGE_LOG", str(_REPO_ROOT / "nova_usage.jsonl"))
MAX_FRAGMENTS = int(os.environ.get("NOVA_MAX_FRAGMENTS", "10"))
COMPACT_THRESHOLD = int(os.environ.get("NOVA_COMPACT_THRESHOLD", "30"))
COMPACT_KEEP_RECENT = int(os.environ.get("NOVA_COMPACT_KEEP", "15"))
DECAY_RATE = float(os.environ.get("NOVA_DECAY_RATE", "0.05"))
DECAY_INTERVAL_DAYS = int(os.environ.get("NOVA_DECAY_DAYS", "7"))
MERGE_SIMILARITY_THRESHOLD = float(os.environ.get("NOVA_MERGE_THRESHOLD", "0.85"))
# OPENAI_API_KEY removed — using local embeddings via nova_embeddings_local

os.makedirs(SHARD_DIR, exist_ok=True)

# === Permission context ===
# Populated from env vars at startup.  Default: all tools permitted.
_denied_tools_env = os.environ.get("NOVA_DENIED_TOOLS", "")
_denied_prefixes_env = os.environ.get("NOVA_DENIED_PREFIXES", "")
_permission_context: ToolPermissionContext = ToolPermissionContext.from_iterables(
    deny_tools=[t for t in _denied_tools_env.split(",") if t.strip()],
    deny_prefixes=[p for p in _denied_prefixes_env.split(",") if p.strip()],
)

# === Session usage tracking ===
_session_usage: UsageSummary = UsageSummary()

# === Session store ===
# Defaults to a nova_sessions/ subdirectory next to the shard store.
_SESSION_STORE_DIR = os.environ.get(
    "NOVA_SESSION_STORE_DIR", str(_REPO_ROOT / "nova_sessions")
)
_session_store: SessionStore = SessionStore(_SESSION_STORE_DIR)

mcp = FastMCP("nova_mcp_v2")

# ═══════════════════════════════════════════════════════════
# PERMISSION HELPERS
# ═══════════════════════════════════════════════════════════

_ALL_TOOL_NAMES: tuple[str, ...] = (
    "nova_shard_interact",
    "nova_shard_create",
    "nova_shard_update",
    "nova_shard_search",
    "nova_shard_list",
    "nova_shard_get",
    "nova_shard_merge",
    "nova_shard_archive",
    "nova_shard_forget",
    "nova_shard_consolidate",
    "nova_graph_query",
    "nova_graph_relate",
    "nova_session_flush",
    "nova_session_load",
    "nova_session_list",
    "nova_forgemaster_sprint",
)


def get_permitted_tools(permission_context: ToolPermissionContext | None = None) -> list[str]:
    """Return the subset of NOVA tool names not blocked by *permission_context*."""
    ctx = permission_context if permission_context is not None else _permission_context
    return [name for name in _ALL_TOOL_NAMES if not ctx.blocks(name)]


def _permission_error(tool_name: str) -> str:
    """Return a structured JSON error for a blocked tool call."""
    return json.dumps(
        {"error": f"Tool '{tool_name}' is not permitted in the current permission context."},
        indent=2,
    )


# ═══════════════════════════════════════════════════════════
# SHARD I/O
# ═══════════════════════════════════════════════════════════

def sanitize_filename(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r'[^a-z0-9_]+', '_', name)
    return name[:40]


def get_unique_filename(base: str) -> str:
    filename = base + ".json"
    i = 1
    while os.path.exists(os.path.join(SHARD_DIR, filename)):
        filename = f"{base}_{i}.json"
        i += 1
    return filename


def load_shard(shard_id: str) -> tuple[dict, str]:
    filepath = os.path.join(SHARD_DIR, shard_id + ".json")
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Shard '{shard_id}' not found.")
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f), filepath


def save_shard(filepath: str, data: dict):
    lock_path = filepath + ".lock"
    with FileLock(lock_path, timeout=5):
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)


def update_shard_usage(data: dict):
    meta = data.setdefault("meta_tags", {})
    meta["usage_count"] = meta.get("usage_count", 0) + 1
    meta["last_used"] = datetime.now().isoformat()


def extract_fragments(shard_data: dict, shard_id: str) -> list[str]:
    fragments = []
    for entry in shard_data.get("conversation_history", []):
        if entry.get("user"):
            fragments.append(f"[SHARD: {shard_id}] User: {entry['user']}")
        if entry.get("ai"):
            fragments.append(f"[SHARD: {shard_id}] NOVA: {entry['ai']}")
    return fragments


# ═══════════════════════════════════════════════════════════
# CONFIDENCE DECAY
# ═══════════════════════════════════════════════════════════

def get_confidence(shard_data: dict) -> float:
    """Get current confidence score. Default 1.0 for shards without it."""
    return shard_data.get("meta_tags", {}).get("confidence", 1.0)


def apply_confidence_decay(shard_data: dict) -> float:
    """
    Decay confidence for shards not accessed in DECAY_INTERVAL_DAYS.
    Formula: MAX(0.1, confidence * (1 - decay_rate))
    Stolen from OpenFang's consolidation.rs — credit where it's due.
    Returns new confidence value.
    """
    meta = shard_data.setdefault("meta_tags", {})
    current_confidence = meta.get("confidence", 1.0)
    last_used_str = meta.get("last_used")

    if not last_used_str:
        return current_confidence

    try:
        last_used = datetime.fromisoformat(last_used_str)
        days_since = (datetime.now() - last_used).days

        if days_since >= DECAY_INTERVAL_DAYS:
            periods = days_since // DECAY_INTERVAL_DAYS
            new_confidence = current_confidence
            for _ in range(periods):
                new_confidence = max(0.1, new_confidence * (1.0 - DECAY_RATE))
            meta["confidence"] = round(new_confidence, 4)
            return new_confidence
    except (ValueError, TypeError):
        pass

    return current_confidence


def confidence_weighted_score(base_score: float, confidence: float) -> float:
    """Weight search relevance by confidence. High confidence + high relevance = top result."""
    return base_score * confidence


# ═══════════════════════════════════════════════════════════
# AUTO-COMPACTION
# ═══════════════════════════════════════════════════════════

def maybe_compact_shard(shard_data: dict, shard_id: str) -> bool:
    """
    If conversation_history exceeds COMPACT_THRESHOLD, auto-compact:
    - Summarize older turns into context.summary
    - Keep only last COMPACT_KEEP_RECENT turns in full
    Returns True if compaction happened.
    """
    history = shard_data.get("conversation_history", [])
    if len(history) < COMPACT_THRESHOLD:
        return False

    older_turns = history[:-COMPACT_KEEP_RECENT]
    recent_turns = history[-COMPACT_KEEP_RECENT:]

    # Generate compaction summary — use GPT if available, fallback to naive
    compaction_summary = _generate_compaction_summary(older_turns, shard_id)

    shard_data["conversation_history"] = recent_turns
    ctx = shard_data.setdefault("context", {})
    existing_summary = ctx.get("summary", "")
    if existing_summary:
        ctx["summary"] = f"{existing_summary}\n\n[COMPACTED — {len(older_turns)} earlier turns]: {compaction_summary}"
    else:
        ctx["summary"] = f"[COMPACTED — {len(older_turns)} turns]: {compaction_summary}"

    ctx["last_compacted"] = datetime.now().isoformat()
    ctx["compacted_turn_count"] = ctx.get("compacted_turn_count", 0) + len(older_turns)
    shard_data["meta_tags"]["last_compacted"] = datetime.now().isoformat()

    return True


# ═══════════════════════════════════════════════════════════
# POST-WRITE ENRICHMENT HOOKS
# ═══════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════
# COSINE SIMILARITY + MERGE SUGGESTIONS
# ═══════════════════════════════════════════════════════════

def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def find_merge_candidates(shard_id: str, shard_data: dict, index: dict) -> list[dict]:
    """
    Compare this shard's embedding against all others.
    Returns list of candidates with similarity > MERGE_SIMILARITY_THRESHOLD.
    """
    my_embedding = shard_data.get("context", {}).get("embedding")
    if not my_embedding:
        return []

    candidates = []
    for other_id, entry in index.items():
        if other_id == shard_id:
            continue
        if "archived" in entry.get("tags", []):
            continue

        other_path = os.path.join(SHARD_DIR, other_id + ".json")
        if not os.path.exists(other_path):
            continue

        try:
            with open(other_path, "r", encoding="utf-8") as f:
                other_data = json.load(f)
            other_embedding = other_data.get("context", {}).get("embedding")
            if not other_embedding:
                continue

            sim = cosine_similarity(my_embedding, other_embedding)
            if sim >= MERGE_SIMILARITY_THRESHOLD:
                candidates.append({
                    "shard_id": other_id,
                    "similarity": round(sim, 4),
                    "guiding_question": other_data.get("guiding_question", "")
                })
        except Exception:
            continue

    candidates.sort(key=lambda x: x["similarity"], reverse=True)
    return candidates


# ═══════════════════════════════════════════════════════════
# KNOWLEDGE GRAPH
# ═══════════════════════════════════════════════════════════

def load_graph() -> dict:
    if not os.path.exists(GRAPH_FILE):
        return {"entities": {}, "relations": []}
    try:
        with open(GRAPH_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"entities": {}, "relations": []}


def save_graph(graph: dict):
    with FileLock(GRAPH_FILE + ".lock", timeout=5):
        with open(GRAPH_FILE, "w", encoding="utf-8") as f:
            json.dump(graph, f, indent=2)


def add_shard_to_graph(shard_id: str, shard_data: dict):
    """Register a shard as an entity in the knowledge graph on create."""
    graph = load_graph()
    graph["entities"][shard_id] = {
        "type": "Shard",
        "guiding_question": shard_data.get("guiding_question", ""),
        "theme": shard_data.get("meta_tags", {}).get("theme", "general"),
        "intent": shard_data.get("meta_tags", {}).get("intent", "reflection"),
        "created_at": datetime.now().isoformat(),
        "confidence": shard_data.get("meta_tags", {}).get("confidence", 1.0)
    }
    save_graph(graph)


def add_relation(source_id: str, target_id: str, relation_type: str, notes: str = ""):
    """Add a directed relation between two shards."""
    graph = load_graph()
    relation = {
        "source": source_id,
        "target": target_id,
        "type": relation_type,
        "notes": notes,
        "created_at": datetime.now().isoformat()
    }
    # Avoid exact duplicates
    existing = graph.get("relations", [])
    for r in existing:
        if r["source"] == source_id and r["target"] == target_id and r["type"] == relation_type:
            return
    existing.append(relation)
    graph["relations"] = existing
    save_graph(graph)


def query_graph(pattern: dict) -> list[dict]:
    """
    Simple pattern query over the knowledge graph.
    Pattern keys: source, target, type (all optional)
    Returns matching relations.
    """
    graph = load_graph()
    results = []
    for relation in graph.get("relations", []):
        match = True
        if "source" in pattern and relation["source"] != pattern["source"]:
            match = False
        if "target" in pattern and relation["target"] != pattern["target"]:
            match = False
        if "type" in pattern and relation["type"] != pattern["type"]:
            match = False
        if match:
            results.append(relation)
    return results


def query_graph_transitive(
    root_id: str,
    relation_type: str = None,
    direction: str = "outbound",
    max_depth: int = 3,
) -> list[dict]:
    """
    BFS traversal of the knowledge graph from root_id.
    direction: "outbound" (root is source), "inbound" (root is target), "both"
    Returns list of dicts: {shard_id, depth, path, relation_type}
    """
    graph = load_graph()
    relations = graph.get("relations", [])
    visited = set()
    queue = [(root_id, 0, [root_id])]
    results = []

    while queue:
        current, depth, path = queue.pop(0)
        if depth >= max_depth:
            continue

        for r in relations:
            if relation_type and r["type"] != relation_type:
                continue

            next_id = None
            if direction in ("outbound", "both") and r["source"] == current:
                next_id = r["target"]
            elif direction in ("inbound", "both") and r["target"] == current:
                next_id = r["source"]

            if next_id and next_id not in visited:
                visited.add(next_id)
                new_path = path + [next_id]
                results.append({
                    "shard_id": next_id,
                    "depth": depth + 1,
                    "path": new_path,
                    "relation_type": r["type"],
                })
                queue.append((next_id, depth + 1, new_path))

    return results


# ═══════════════════════════════════════════════════════════
# USAGE TRACKING
# ═══════════════════════════════════════════════════════════

def log_operation(tool_name: str, shard_ids: list[str], metadata: dict = None):
    """Append operation log to JSONL file for cost/usage analysis."""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "tool": tool_name,
        "shards": shard_ids,
        "metadata": metadata or {}
    }
    try:
        with open(USAGE_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════
# INDEX MANAGEMENT
# ═══════════════════════════════════════════════════════════

def load_index() -> dict:
    if not os.path.exists(INDEX_FILE):
        return {}
    try:
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_index(index: dict):
    with FileLock(INDEX_FILE + ".lock", timeout=5):
        with open(INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2)


def classify_tags(shard: dict) -> list[str]:
    tags = []
    now = datetime.now()
    meta = shard.get("meta_tags", {})
    usage_count = meta.get("usage_count", 0)
    last_used_str = meta.get("last_used")
    confidence = meta.get("confidence", 1.0)

    if last_used_str:
        try:
            last_used = datetime.fromisoformat(last_used_str)
            if now - last_used < timedelta(days=3):
                tags.append("recent")
            if now - last_used > timedelta(days=14):
                tags.append("stale")
        except (ValueError, TypeError):
            pass

    if usage_count > 10:
        tags.append("frequently_used")
    if meta.get("intent") == "archived":
        tags.append("archived")
    if meta.get("intent") == "forgotten":
        tags.append("forgotten")
    if shard.get("context", {}).get("embedding"):
        tags.append("enriched")
    if confidence < 0.4:
        tags.append("low_confidence")
    if meta.get("last_compacted"):
        tags.append("compacted")

    return tags


def update_index() -> dict:
    index = {}
    if not os.path.exists(SHARD_DIR):
        return index

    for fname in sorted(os.listdir(SHARD_DIR)):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(SHARD_DIR, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                shard = json.load(f)
        except Exception:
            continue

        shard_id = shard.get("shard_id", fname.replace(".json", ""))
        index[shard_id] = {
            "shard_id": shard_id,
            "filename": fname,
            "guiding_question": shard.get("guiding_question", ""),
            "tags": classify_tags(shard),
            "meta": shard.get("meta_tags", {}),
            "context_summary": shard.get("context", {}).get("summary", ""),
            "context_topics": shard.get("context", {}).get("topics", []),
            "confidence": shard.get("meta_tags", {}).get("confidence", 1.0),
        }

    save_index(index)
    return index


def patch_index_entry(shard_id: str, shard_data: dict) -> dict:
    """Update a single shard entry in the index without full rescan."""
    index = load_index()
    index[shard_id] = {
        "shard_id": shard_id,
        "filename": shard_id + ".json",
        "guiding_question": shard_data.get("guiding_question", ""),
        "tags": classify_tags(shard_data),
        "meta": shard_data.get("meta_tags", {}),
        "context_summary": shard_data.get("context", {}).get("summary", ""),
        "context_topics": shard_data.get("context", {}).get("topics", []),
        "confidence": shard_data.get("meta_tags", {}).get("confidence", 1.0),
    }
    save_index(index)
    return index


def guess_relevant_shards(message: str, index: dict, top_n: int = 3) -> list[str]:
    """Fuzzy match with confidence weighting."""
    scored = []
    msg_lower = message.lower()
    msg_tokens = set(msg_lower.split())

    for shard_id, entry in index.items():
        tags = entry.get("tags", [])
        if "archived" in tags or "forgotten" in tags:
            continue

        confidence = entry.get("confidence", 1.0)

        searchable = " ".join([
            entry.get("guiding_question", ""),
            entry.get("context_summary", ""),
            " ".join(entry.get("context_topics", [])),
            entry.get("meta", {}).get("theme", ""),
            entry.get("meta", {}).get("intent", ""),
        ]).lower()

        search_tokens = set(searchable.split())
        overlap = msg_tokens & search_tokens
        base_score = len(overlap) / max(len(msg_tokens), 1)
        weighted_score = confidence_weighted_score(base_score, confidence)

        if weighted_score > 0.05:
            scored.append((shard_id, weighted_score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [s[0] for s in scored[:top_n]]


# ═══════════════════════════════════════════════════════════
# INPUT MODELS
# ═══════════════════════════════════════════════════════════

class ShardInteractInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    shard_ids: str = Field(default="")
    message: str = Field(..., min_length=1)
    auto_select: bool = Field(default=True)
    session_id: Optional[str] = Field(default=None)


class ShardCreateInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    guiding_question: str = Field(..., min_length=1)
    intent: str = Field(default="reflection")
    theme: str = Field(default="general")
    initial_message: str = Field(default="")
    related_shards: str = Field(default="")
    relation_type: str = Field(default="references")


class ShardUpdateInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    shard_id: str = Field(..., min_length=1)
    user_message: str = Field(default="")
    ai_response: str = Field(default="")


class ShardSearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    query: str = Field(..., min_length=1)
    top_n: int = Field(default=5, ge=1, le=20)
    include_low_confidence: bool = Field(default=False)


class ShardMergeInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    shard_ids: str = Field(..., min_length=1)
    new_guiding_question: str = Field(..., min_length=1)
    new_theme: str = Field(..., min_length=1)
    archive_originals: bool = Field(default=False)


class ShardArchiveInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    shard_id: str = Field(..., min_length=1)


class ShardForgetInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    shard_id: str = Field(..., min_length=1)
    reason: str = Field(default="")


class GraphQueryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    source: str = Field(default="")
    target: str = Field(default="")
    relation_type: str = Field(default="")
    transitive: bool = Field(default=False)
    max_depth: int = Field(default=3, ge=1, le=10)


class GraphRelationInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    source_id: str = Field(..., min_length=1)
    target_id: str = Field(..., min_length=1)
    relation_type: str = Field(..., min_length=1)
    notes: str = Field(default="")


class ShardConsolidateInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    dry_run: bool = Field(default=False)


class ShardGetInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    shard_id: str = Field(..., min_length=1)


# ═══════════════════════════════════════════════════════════
# MCP TOOLS
# ═══════════════════════════════════════════════════════════

@mcp.tool(name="nova_shard_interact")
async def nova_shard_interact(params: ShardInteractInput) -> str:
    """Load shards into context for synthesis. Auto-selects relevant shards if none specified. Confidence-weighted."""
    global _session_usage

    if _permission_context.blocks("nova_shard_interact"):
        return _permission_error("nova_shard_interact")

    shard_ids = [s.strip() for s in params.shard_ids.split(",") if s.strip()] if params.shard_ids else []
    inferred = False

    if not shard_ids and params.auto_select:
        inferred = True
        index = load_index() or update_index()
        shard_ids = guess_relevant_shards(params.message, index) or []

    if not shard_ids:
        return json.dumps({
            "status": "no_shards_found",
            "message": "No shards matched the query.",
            "suggestion": "Use nova_shard_search to find relevant shards, or nova_shard_create to start a new one."
        }, indent=2)

    loaded = []
    errors = []

    for sid in shard_ids:
        try:
            data, filepath = load_shard(sid)
            update_shard_usage(data)
            # Boost confidence on access
            meta = data.setdefault("meta_tags", {})
            meta["confidence"] = min(1.0, meta.get("confidence", 1.0) + 0.05)
            save_shard(filepath, data)

            fragments = extract_fragments(data, sid)[-MAX_FRAGMENTS:]

            loaded.append({
                "shard_id": sid,
                "guiding_question": data.get("guiding_question", ""),
                "meta_tags": meta,
                "confidence": meta.get("confidence", 1.0),
                "tags": classify_tags(data),
                "fragment_count": len(fragments),
                "fragments": fragments,
                "context_summary": data.get("context", {}).get("summary", ""),
            })
        except FileNotFoundError:
            errors.append(f"Shard '{sid}' not found.")

    response_payload = {
        "status": "loaded",
        "inferred": inferred,
        "shards": loaded,
        "errors": errors
    }
    response_str = json.dumps(response_payload, indent=2)

    # Update session token usage (word-count estimate)
    _session_usage = _session_usage.add_turn(params.message, response_str)

    # Session tracking — if a session_id was supplied, log this interaction
    _session_id = params.session_id
    _active_session = None
    if _session_id:
        existing = _session_store.get(_session_id)
        _active_session = existing if existing is not None else _session_store.create(_session_id)
        _active_session = _active_session.add_message("user", params.message)
        _active_session = _active_session.add_message("assistant", response_str)
        _session_store.update(_active_session)

    log_entry: dict = {
        "session_input_tokens": _session_usage.input_tokens,
        "session_output_tokens": _session_usage.output_tokens,
        "session_total_tokens": _session_usage.total_tokens,
    }
    if _session_id and _active_session is not None:
        log_entry["session_id"] = _session_id
        log_entry["session_message_count"] = len(_active_session.messages)

    log_operation("nova_shard_interact", shard_ids, log_entry)

    return response_str


@mcp.tool(name="nova_shard_create")
async def nova_shard_create(params: ShardCreateInput) -> str:
    """Create a new shard. Triggers post-write enrichment hook and registers in knowledge graph."""
    if _permission_context.blocks("nova_shard_create"):
        return _permission_error("nova_shard_create")
    base_name = sanitize_filename(f"{params.theme}_{params.intent}")
    filename = get_unique_filename(base_name)
    filepath = os.path.join(SHARD_DIR, filename)
    shard_id = filename.replace(".json", "")

    shard_data = {
        "shard_id": shard_id,
        "guiding_question": params.guiding_question,
        "conversation_history": [],
        "meta_tags": {
            "intent": params.intent,
            "theme": params.theme,
            "usage_count": 1,
            "last_used": datetime.now().isoformat(),
            "confidence": 1.0,
            "enrichment_status": "pending"
        }
    }

    if params.initial_message:
        shard_data["conversation_history"].append({
            "timestamp": datetime.now().isoformat(),
            "user": params.initial_message,
            "ai": ""
        })

    # Post-write enrichment hook
    # Blocking — refactor to async post-write hook in future iteration
    enrich_shard(shard_id, shard_data)

    save_shard(filepath, shard_data)
    patch_index_entry(shard_id, shard_data)

    # Register in knowledge graph
    add_shard_to_graph(shard_id, shard_data)

    # Wire up relations to related shards
    for related_id in ([s.strip() for s in params.related_shards.split(",") if s.strip()] if params.related_shards else []):
        add_relation(shard_id, related_id, params.relation_type)

    # Check for merge candidates
    index = load_index()
    merge_candidates = find_merge_candidates(shard_id, shard_data, index)

    log_operation("nova_shard_create", [shard_id])

    result = {
        "status": "created",
        "shard_id": shard_id,
        "guiding_question": params.guiding_question,
        "enrichment_status": shard_data.get("meta_tags", {}).get("enrichment_status", "unknown"),
    }

    if merge_candidates:
        result["merge_suggestions"] = merge_candidates
        result["merge_note"] = f"{len(merge_candidates)} similar shard(s) found. Consider merging."

    return json.dumps(result, indent=2)


@mcp.tool(name="nova_shard_update")
async def nova_shard_update(params: ShardUpdateInput) -> str:
    """Append to a shard. Triggers post-write enrichment hook and auto-compaction if threshold exceeded."""
    if _permission_context.blocks("nova_shard_update"):
        return _permission_error("nova_shard_update")
    try:
        data, filepath = load_shard(params.shard_id)
    except FileNotFoundError:
        return json.dumps({"status": "error", "message": f"Shard '{params.shard_id}' not found."}, indent=2)

    data.setdefault("conversation_history", []).append({
        "timestamp": datetime.now().isoformat(),
        "user": params.user_message,
        "ai": params.ai_response
    })
    update_shard_usage(data)

    # Auto-compaction check
    compacted = maybe_compact_shard(data, params.shard_id)

    # Post-write enrichment hook
    # Blocking — refactor to async post-write hook in future iteration
    enrich_shard(params.shard_id, data)

    save_shard(filepath, data)
    patch_index_entry(params.shard_id, data)

    # Update graph entity confidence
    graph = load_graph()
    if params.shard_id in graph.get("entities", {}):
        graph["entities"][params.shard_id]["confidence"] = data["meta_tags"].get("confidence", 1.0)
        save_graph(graph)

    log_operation("nova_shard_update", [params.shard_id])

    return json.dumps({
        "status": "updated",
        "shard_id": params.shard_id,
        "total_entries": len(data["conversation_history"]),
        "compacted": compacted,
        "enrichment_status": data.get("meta_tags", {}).get("enrichment_status", "unknown"),
    }, indent=2)


@mcp.tool(name="nova_shard_search")
async def nova_shard_search(params: ShardSearchInput) -> str:
    """Search shards with confidence weighting. High-confidence shards rank higher for same relevance score."""
    if _permission_context.blocks("nova_shard_search"):
        return _permission_error("nova_shard_search")
    index = load_index() or update_index()
    results = []
    query_tokens = set(params.query.lower().split())

    for shard_id, entry in index.items():
        tags = entry.get("tags", [])
        if "archived" in tags or "forgotten" in tags:
            continue
        if not params.include_low_confidence and "low_confidence" in tags:
            continue

        confidence = entry.get("confidence", 1.0)

        searchable = " ".join([
            entry.get("guiding_question", ""),
            entry.get("context_summary", ""),
            " ".join(entry.get("context_topics", [])),
            entry.get("meta", {}).get("theme", ""),
            entry.get("meta", {}).get("intent", ""),
            shard_id,
        ]).lower()

        search_tokens = set(searchable.split())
        overlap = query_tokens & search_tokens
        base_score = len(overlap) / max(len(query_tokens), 1)
        weighted = confidence_weighted_score(base_score, confidence)

        if weighted > 0:
            results.append({
                "shard_id": shard_id,
                "guiding_question": entry.get("guiding_question", ""),
                "relevance_score": round(base_score, 4),
                "confidence": round(confidence, 4),
                "weighted_score": round(weighted, 4),
                "tags": tags,
                "context_summary": entry.get("context_summary", ""),
            })

    results.sort(key=lambda x: x["weighted_score"], reverse=True)
    log_operation("nova_shard_search", [], {"query": params.query})

    return json.dumps({
        "query": params.query,
        "results": results[:params.top_n],
        "total_searched": len(index)
    }, indent=2)


@mcp.tool(name="nova_shard_list")
async def nova_shard_list() -> str:
    """List all shards with confidence scores and status tags."""
    if _permission_context.blocks("nova_shard_list"):
        return _permission_error("nova_shard_list")
    index = update_index()

    shards = []
    for shard_id, entry in index.items():
        shards.append({
            "shard_id": shard_id,
            "guiding_question": entry.get("guiding_question", ""),
            "tags": entry.get("tags", []),
            "theme": entry.get("meta", {}).get("theme", ""),
            "intent": entry.get("meta", {}).get("intent", ""),
            "confidence": entry.get("confidence", 1.0),
            "usage_count": entry.get("meta", {}).get("usage_count", 0),
        })

    # Sort by confidence descending
    shards.sort(key=lambda x: x["confidence"], reverse=True)

    return json.dumps({
        "total": len(shards),
        "shards": shards
    }, indent=2)


@mcp.tool(name="nova_shard_get")
async def nova_shard_get(params: ShardGetInput) -> str:
    """Read the full raw content of a shard from disk. Read-only, no side effects. For inspection and correction workflows."""
    if _permission_context.blocks("nova_shard_get"):
        return _permission_error("nova_shard_get")
    try:
        data, _ = load_shard(params.shard_id)
    except FileNotFoundError:
        return json.dumps({
            "status": "error",
            "shard_id": params.shard_id,
            "message": f"Shard '{params.shard_id}' not found."
        }, indent=2)

    fragments = extract_fragments(data, params.shard_id)

    return json.dumps({
        "shard_id": params.shard_id,
        "guiding_question": data.get("guiding_question", ""),
        "meta_tags": data.get("meta_tags", {}),
        "tags": classify_tags(data),
        "fragment_count": len(fragments),
        "fragments": fragments,
        "context_summary": data.get("context", {}).get("summary", ""),
    }, indent=2)


@mcp.tool(name="nova_shard_merge")
async def nova_shard_merge(params: ShardMergeInput) -> str:
    """Merge multiple shards into a meta-shard. Updates knowledge graph relations."""
    if _permission_context.blocks("nova_shard_merge"):
        return _permission_error("nova_shard_merge")
    merged_history = []
    source_questions = []
    shard_ids_list = [s.strip() for s in params.shard_ids.split(",") if s.strip()]

    for sid in shard_ids_list:
        try:
            data, _ = load_shard(sid)
            merged_history.extend(data.get("conversation_history", []))
            source_questions.append(f"{sid}: {data.get('guiding_question', '')}")
        except FileNotFoundError:
            return json.dumps({"status": "error", "message": f"Shard '{sid}' not found."}, indent=2)

    merged_history.sort(key=lambda x: x.get("timestamp", ""))

    base_name = sanitize_filename(f"{params.new_theme}_merged")
    filename = get_unique_filename(base_name)
    filepath = os.path.join(SHARD_DIR, filename)
    new_id = filename.replace(".json", "")

    meta_shard = {
        "shard_id": new_id,
        "guiding_question": params.new_guiding_question,
        "conversation_history": merged_history,
        "meta_tags": {
            "intent": "meta_synthesis",
            "theme": params.new_theme,
            "usage_count": 1,
            "last_used": datetime.now().isoformat(),
            "confidence": 1.0,
            "merged_from": shard_ids_list,
            "source_questions": source_questions
        }
    }

    # Blocking — refactor to async post-write hook in future iteration
    enrich_shard(new_id, meta_shard)
    save_shard(filepath, meta_shard)

    if params.archive_originals:
        for sid in shard_ids_list:
            try:
                data, fp = load_shard(sid)
                data.setdefault("meta_tags", {})["intent"] = "archived"
                data["meta_tags"]["archived_at"] = datetime.now().isoformat()
                save_shard(fp, data)
            except FileNotFoundError:
                pass

    patch_index_entry(new_id, meta_shard)
    add_shard_to_graph(new_id, meta_shard)

    # Wire graph: each source extends the new meta-shard
    for sid in shard_ids_list:
        add_relation(sid, new_id, "extends", "merged into meta-shard")

    log_operation("nova_shard_merge", shard_ids_list + [new_id])

    return json.dumps({
        "status": "merged",
        "new_shard_id": new_id,
        "sources": shard_ids_list,
        "total_entries": len(merged_history),
        "originals_archived": params.archive_originals
    }, indent=2)


@mcp.tool(name="nova_shard_archive")
async def nova_shard_archive(params: ShardArchiveInput) -> str:
    """Soft-archive a shard. Excluded from search. Memory decays through deprioritization, not deletion."""
    if _permission_context.blocks("nova_shard_archive"):
        return _permission_error("nova_shard_archive")
    try:
        data, filepath = load_shard(params.shard_id)
    except FileNotFoundError:
        return json.dumps({"status": "error", "message": f"Shard '{params.shard_id}' not found."}, indent=2)

    data.setdefault("meta_tags", {})["intent"] = "archived"
    data["meta_tags"]["archived_at"] = datetime.now().isoformat()
    save_shard(filepath, data)
    patch_index_entry(params.shard_id, data)

    return json.dumps({
        "status": "archived",
        "shard_id": params.shard_id,
        "guiding_question": data.get("guiding_question", "")
    }, indent=2)


@mcp.tool(name="nova_shard_forget")
async def nova_shard_forget(params: ShardForgetInput) -> str:
    """
    Hard soft-delete with provenance log.
    Shard is marked as forgotten and removed from all search/interact results.
    Content preserved on disk for audit. Logged to usage file with reason.
    This is different from archive — forgotten shards are intentionally excluded,
    not just deprioritized.
    """
    if _permission_context.blocks("nova_shard_forget"):
        return _permission_error("nova_shard_forget")
    try:
        data, filepath = load_shard(params.shard_id)
    except FileNotFoundError:
        return json.dumps({"status": "error", "message": f"Shard '{params.shard_id}' not found."}, indent=2)

    data.setdefault("meta_tags", {})["intent"] = "forgotten"
    data["meta_tags"]["forgotten_at"] = datetime.now().isoformat()
    data["meta_tags"]["forget_reason"] = params.reason
    data["meta_tags"]["confidence"] = 0.0
    save_shard(filepath, data)
    patch_index_entry(params.shard_id, data)

    log_operation("nova_shard_forget", [params.shard_id], {"reason": params.reason})

    return json.dumps({
        "status": "forgotten",
        "shard_id": params.shard_id,
        "reason": params.reason,
        "note": "Shard preserved on disk for audit. Excluded from all search and interact operations."
    }, indent=2)


@mcp.tool(name="nova_shard_consolidate")
async def nova_shard_consolidate(params: ShardConsolidateInput) -> str:
    """
    Run the full maintenance cycle:
    1. Apply confidence decay to all shards not accessed in DECAY_INTERVAL_DAYS
    2. Auto-compact any shards exceeding COMPACT_THRESHOLD turns
    3. Surface merge suggestions for high-similarity pairs
    4. Return a summary of what changed.
    
    Run this periodically (on startup, daily, or when things feel cluttered).
    """
    if _permission_context.blocks("nova_shard_consolidate"):
        return _permission_error("nova_shard_consolidate")
    index = load_index() or update_index()
    decayed = []
    compacted = []
    merge_suggestions = []

    for shard_id in list(index.keys()):
        tags = index[shard_id].get("tags", [])
        if "forgotten" in tags:
            continue

        try:
            data, filepath = load_shard(shard_id)
        except FileNotFoundError:
            continue

        # 1. Confidence decay
        old_confidence = data.get("meta_tags", {}).get("confidence", 1.0)
        new_confidence = apply_confidence_decay(data)
        if new_confidence < old_confidence:
            decayed.append({
                "shard_id": shard_id,
                "old_confidence": round(old_confidence, 4),
                "new_confidence": round(new_confidence, 4)
            })

        # 2. Auto-compaction
        was_compacted = maybe_compact_shard(data, shard_id)
        if was_compacted:
            compacted.append(shard_id)

        save_shard(filepath, data)

    # Rebuild index after mutations
    index = update_index()

    # 3. Merge suggestions — only for enriched shards
    checked = set()
    for shard_id, entry in index.items():
        if "enriched" not in entry.get("tags", []):
            continue
        if shard_id in checked:
            continue

        try:
            data, _ = load_shard(shard_id)
            candidates = find_merge_candidates(shard_id, data, index)
            for c in candidates:
                pair = tuple(sorted([shard_id, c["shard_id"]]))
                if pair not in checked:
                    merge_suggestions.append({
                        "shard_a": shard_id,
                        "shard_b": c["shard_id"],
                        "similarity": c["similarity"],
                        "question_a": data.get("guiding_question", ""),
                        "question_b": c["guiding_question"]
                    })
                    checked.add(pair)
        except FileNotFoundError:
            continue

        checked.add(shard_id)

    log_operation("nova_shard_consolidate", [], {
        "decayed": len(decayed),
        "compacted": len(compacted),
        "merge_suggestions": len(merge_suggestions)
    })

    return json.dumps({
        "status": "consolidation_complete",
        "decayed_shards": decayed,
        "compacted_shards": compacted,
        "merge_suggestions": merge_suggestions[:10],  # Cap at 10
        "total_shards": len(index),
        "summary": f"Decayed {len(decayed)} shards, compacted {len(compacted)}, found {len(merge_suggestions)} merge candidates."
    }, indent=2)


@mcp.tool(name="nova_graph_query")
async def nova_graph_query(params: GraphQueryInput) -> str:
    """
    Query the inter-shard knowledge graph.
    Find what a shard influences, depends on, extends, contradicts, or references.
    All parameters optional — omit to return all relations.
    Set transitive=True to traverse the graph by BFS up to max_depth hops.

    Relation types: influences, depends_on, contradicts, extends, references, merged_from
    """
    if _permission_context.blocks("nova_graph_query"):
        return _permission_error("nova_graph_query")
    if params.transitive:
        root_id = params.source or params.target
        if not root_id:
            return json.dumps({"status": "error", "message": "Transitive query requires 'source' or 'target'."}, indent=2)
        direction = "outbound" if params.source else "inbound"
        results = query_graph_transitive(
            root_id=root_id,
            relation_type=params.relation_type or None,
            direction=direction,
            max_depth=params.max_depth,
        )
        graph = load_graph()
        return json.dumps({
            "mode": "transitive",
            "root": root_id,
            "direction": direction,
            "relation_type": params.relation_type or "any",
            "max_depth": params.max_depth,
            "results": results,
            "total_entities": len(graph.get("entities", {})),
            "total_relations": len(graph.get("relations", []))
        }, indent=2)

    pattern = {}
    if params.source:
        pattern["source"] = params.source
    if params.target:
        pattern["target"] = params.target
    if params.relation_type:
        pattern["type"] = params.relation_type

    relations = query_graph(pattern)
    graph = load_graph()

    # Enrich with entity metadata
    enriched = []
    for r in relations:
        source_entity = graph.get("entities", {}).get(r["source"], {})
        target_entity = graph.get("entities", {}).get(r["target"], {})
        enriched.append({
            **r,
            "source_question": source_entity.get("guiding_question", ""),
            "target_question": target_entity.get("guiding_question", ""),
        })

    return json.dumps({
        "mode": "direct",
        "pattern": pattern,
        "relations": enriched,
        "total_entities": len(graph.get("entities", {})),
        "total_relations": len(graph.get("relations", []))
    }, indent=2)


@mcp.tool(name="nova_graph_relate")
async def nova_graph_relate(params: GraphRelationInput) -> str:
    """
    Manually add a directed relation between two shards in the knowledge graph.
    Use this when you notice a connection that wasn't auto-detected.
    
    Relation types:
      influences   — shard A shapes the thinking in shard B
      depends_on   — shard A requires shard B to make sense
      contradicts  — shards are in tension, revisit both
      extends      — shard A builds on shard B
      references   — shard A cites or mentions shard B
    """
    if _permission_context.blocks("nova_graph_relate"):
        return _permission_error("nova_graph_relate")
    add_relation(params.source_id, params.target_id, params.relation_type, params.notes)

    return json.dumps({
        "status": "relation_added",
        "source": params.source_id,
        "target": params.target_id,
        "type": params.relation_type,
        "notes": params.notes
    }, indent=2)


# ═══════════════════════════════════════════════════════════
# SESSION TOOLS
# ═══════════════════════════════════════════════════════════

class SessionFlushInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    session_id: str = Field(..., min_length=1)


class SessionLoadInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    session_id: str = Field(..., min_length=1)


class SessionListInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')


@mcp.tool(name="nova_session_flush")
async def nova_session_flush(params: SessionFlushInput) -> str:
    """Persist an active session to disk and remove it from memory. Returns JSON confirmation with token totals."""
    if _permission_context.blocks("nova_session_flush"):
        return _permission_error("nova_session_flush")

    session = _session_store.get(params.session_id)
    if session is None:
        return json.dumps({
            "error": f"Session '{params.session_id}' is not active in memory.",
            "hint": "Use nova_session_load to restore a previously flushed session.",
        }, indent=2)

    try:
        _session_store.flush(params.session_id)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, indent=2)

    return json.dumps({
        "status": "flushed",
        "session_id": params.session_id,
        "message_count": len(session.messages),
        "token_totals": {
            "input_tokens": session.usage.input_tokens,
            "output_tokens": session.usage.output_tokens,
            "total_tokens": session.usage.total_tokens,
        },
    }, indent=2)


@mcp.tool(name="nova_session_load")
async def nova_session_load(params: SessionLoadInput) -> str:
    """Load a previously flushed session from disk into memory. Returns JSON with session metadata and message count."""
    if _permission_context.blocks("nova_session_load"):
        return _permission_error("nova_session_load")

    try:
        session = _session_store.load(params.session_id)
    except FileNotFoundError:
        return json.dumps({
            "error": f"No persisted session found for '{params.session_id}'.",
            "available": _session_store.list_sessions(),
        }, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, indent=2)

    return json.dumps({
        "status": "loaded",
        "session_id": session.session_id,
        "message_count": len(session.messages),
        "created_at": session.created_at,
        "last_active": session.last_active,
        "token_totals": {
            "input_tokens": session.usage.input_tokens,
            "output_tokens": session.usage.output_tokens,
            "total_tokens": session.usage.total_tokens,
        },
    }, indent=2)


@mcp.tool(name="nova_session_list")
async def nova_session_list(params: SessionListInput) -> str:
    """List all session IDs currently persisted on disk."""
    if _permission_context.blocks("nova_session_list"):
        return _permission_error("nova_session_list")

    sessions = _session_store.list_sessions()
    return json.dumps({
        "status": "ok",
        "sessions": sessions,
        "count": len(sessions),
    }, indent=2)


# ═══════════════════════════════════════════════════════════
# FORGEMASTER TOOLS
# ═══════════════════════════════════════════════════════════

class ForgemasterSprintInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    sprint_id: str = Field(..., min_length=1)
    design_doc: str = Field(..., min_length=1)
    shard_ids: Optional[str] = Field(default=None)


@mcp.tool(name="nova_forgemaster_sprint")
async def nova_forgemaster_sprint(params: ForgemasterSprintInput) -> str:
    """
    Run a full Forgemaster sprint: orchestrator → planner → implementer → reviewer.
    Loads optional shards into context, executes the 4-turn pipeline, flushes the
    session, and returns a sprint summary.

    ``shard_ids`` is an optional comma-separated list of shard IDs to load into
    context before the sprint begins (same pattern as nova_shard_interact).
    """
    if _permission_context.blocks("nova_forgemaster_sprint"):
        return _permission_error("nova_forgemaster_sprint")

    shard_id_list: list[str] = (
        [s.strip() for s in params.shard_ids.split(",") if s.strip()]
        if params.shard_ids
        else []
    )

    runtime = ForgemasterRuntime(_session_store, _permission_context)
    try:
        summary = runtime.run_sprint(params.sprint_id, params.design_doc, shard_id_list)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, indent=2)

    log_operation("nova_forgemaster_sprint", shard_id_list, {"sprint_id": params.sprint_id})
    return json.dumps(summary, indent=2)




@mcp.resource("nova://skill")
async def nova_skill() -> str:
    skill_path = Path(__file__).parent / "SKILL_v2.md"
    if skill_path.exists():
        return skill_path.read_text(encoding="utf-8")
    # Fallback to v1 skill
    skill_path = Path(__file__).parent / "SKILL.md"
    if skill_path.exists():
        return skill_path.read_text(encoding="utf-8")
    return "SKILL.md not found."


@mcp.resource("nova://index")
async def nova_index() -> str:
    return json.dumps(update_index(), indent=2)


@mcp.resource("nova://graph")
async def nova_graph() -> str:
    return json.dumps(load_graph(), indent=2)


@mcp.resource("nova://usage")
async def nova_usage() -> str:
    """Return last 100 operation log entries plus running session token totals."""
    if not os.path.exists(USAGE_LOG_FILE):
        entries = []
        total_lines = 0
    else:
        lines = []
        try:
            with open(USAGE_LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            pass
        entries = [json.loads(l) for l in lines[-100:] if l.strip()]
        total_lines = len(lines)

    return json.dumps({
        "entries": entries,
        "total": total_lines,
        "session_tokens": {
            "input_tokens": _session_usage.input_tokens,
            "output_tokens": _session_usage.output_tokens,
            "total_tokens": _session_usage.total_tokens,
        },
    }, indent=2)


# ═══════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    mcp.run()