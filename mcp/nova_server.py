"""
nova_server.py — NOVA MCP Server

Exposes the NOVA shard-based cognitive architecture as MCP tools.
No OpenAI dependency — this server manages shards and serves them
to whatever LLM connects. The intelligence is in the structure.

Tools: interact, create, update, search, list, merge, archive
Resources: nova://skill, nova://index
"""

import os
import re
import json
import difflib
from datetime import datetime
from typing import Optional
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ConfigDict

from shard_index import (
    SHARD_DIR, INDEX_FILE,
    load_index, update_index, classify_tags, load_shard_file
)

MAX_FRAGMENTS_PER_SHARD = int(os.environ.get("NOVA_MAX_FRAGMENTS", "10"))

# === Initialize MCP Server ===
mcp = FastMCP("nova_mcp")


# === Shard I/O ===

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
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def update_shard_usage(data: dict):
    meta = data.setdefault("meta_tags", {})
    meta["usage_count"] = meta.get("usage_count", 0) + 1
    meta["last_used"] = datetime.now().isoformat()


def extract_fragments(shard_data: dict, shard_id: str) -> list[str]:
    fragments = []
    for entry in shard_data.get("conversation_history", []):
        user = entry.get("user", "")
        ai = entry.get("ai", "")
        if user:
            fragments.append(f"[SHARD: {shard_id}] User: {user}")
        if ai:
            fragments.append(f"[SHARD: {shard_id}] NOVA: {ai}")
    return fragments


def guess_relevant_shards(message: str, index: dict, top_n: int = 3) -> list[str]:
    """Fuzzy match against guiding questions, summaries, and topics."""
    scored = []
    msg_lower = message.lower()
    msg_tokens = set(msg_lower.split())

    for shard_id, entry in index.items():
        if "archived" in entry.get("tags", []):
            continue

        searchable = " ".join([
            entry.get("guiding_question", ""),
            entry.get("context_summary", ""),
            " ".join(entry.get("context_topics", [])),
            entry.get("meta", {}).get("theme", ""),
            entry.get("meta", {}).get("intent", ""),
        ]).lower()

        search_tokens = set(searchable.split())
        overlap = msg_tokens & search_tokens
        score = len(overlap) / max(len(msg_tokens), 1)

        if score > 0.1:
            scored.append((shard_id, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [s[0] for s in scored[:top_n]]


# === Input Models ===

class ShardInteractInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    shard_ids: list[str] = Field(default_factory=list)
    message: str = Field(..., min_length=1)
    auto_select: bool = Field(default=True)


class ShardCreateInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    guiding_question: str = Field(..., min_length=1)
    intent: str = Field(default="reflection")
    theme: str = Field(default="general")
    initial_message: str = Field(default="")


class ShardUpdateInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    shard_id: str = Field(..., min_length=1)
    user_message: str = Field(default="")
    ai_response: str = Field(default="")


class ShardSearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    query: str = Field(..., min_length=1)
    top_n: int = Field(default=5, ge=1, le=20)


class ShardMergeInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    shard_ids: list[str] = Field(..., min_length=2)
    new_guiding_question: str = Field(..., min_length=1)
    new_theme: str = Field(..., min_length=1)
    archive_originals: bool = Field(default=False)


class ShardArchiveInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    shard_id: str = Field(..., min_length=1)


# === MCP Tools ===

@mcp.tool(
    name="nova_shard_interact",
    annotations={
        "title": "Interact with NOVA Shards",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def nova_shard_interact(params: ShardInteractInput) -> str:
    """Load shards into context for synthesis. Auto-selects relevant shards if none specified."""
    shard_ids = list(params.shard_ids)
    inferred = False

    if not shard_ids and params.auto_select:
        inferred = True
        try:
            index = load_index()
            if not index:
                index = update_index()
            guessed = guess_relevant_shards(params.message, index)
            shard_ids = guessed if guessed else []
        except Exception:
            shard_ids = []

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
            save_shard(filepath, data)

            fragments = extract_fragments(data, sid)[-MAX_FRAGMENTS_PER_SHARD:]

            loaded.append({
                "shard_id": sid,
                "guiding_question": data.get("guiding_question", ""),
                "meta_tags": data.get("meta_tags", {}),
                "status_tags": classify_tags(data),
                "fragment_count": len(fragments),
                "fragments": fragments
            })
        except FileNotFoundError:
            errors.append(f"Shard '{sid}' not found.")

    return json.dumps({
        "status": "loaded",
        "inferred": inferred,
        "shards": loaded,
        "errors": errors
    }, indent=2)


@mcp.tool(
    name="nova_shard_create",
    annotations={
        "title": "Create a New Shard",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False
    }
)
async def nova_shard_create(params: ShardCreateInput) -> str:
    """Create a new shard with a guiding question and metadata."""
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
            "last_used": datetime.now().isoformat()
        }
    }

    if params.initial_message:
        shard_data["conversation_history"].append({
            "timestamp": datetime.now().isoformat(),
            "user": params.initial_message,
            "ai": ""
        })

    save_shard(filepath, shard_data)
    update_index()

    return json.dumps({
        "status": "created",
        "shard_id": shard_id,
        "guiding_question": params.guiding_question
    }, indent=2)


@mcp.tool(
    name="nova_shard_update",
    annotations={
        "title": "Update an Existing Shard",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False
    }
)
async def nova_shard_update(params: ShardUpdateInput) -> str:
    """Append a conversation entry to an existing shard."""
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
    save_shard(filepath, data)

    return json.dumps({
        "status": "updated",
        "shard_id": params.shard_id,
        "total_entries": len(data["conversation_history"])
    }, indent=2)


@mcp.tool(
    name="nova_shard_search",
    annotations={
        "title": "Search NOVA Shards",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def nova_shard_search(params: ShardSearchInput) -> str:
    """Search shards by content, tags, and metadata."""
    index = load_index()
    if not index:
        index = update_index()

    results = []
    query_tokens = set(params.query.lower().split())

    for shard_id, entry in index.items():
        if "archived" in entry.get("tags", []):
            continue

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
        score = len(overlap) / max(len(query_tokens), 1)

        if score > 0:
            results.append({
                "shard_id": shard_id,
                "guiding_question": entry.get("guiding_question", ""),
                "score": round(score, 4),
                "tags": entry.get("tags", []),
                "context_summary": entry.get("context_summary", ""),
            })

    results.sort(key=lambda x: x["score"], reverse=True)

    return json.dumps({
        "query": params.query,
        "results": results[:params.top_n],
        "total_searched": len(index)
    }, indent=2)


@mcp.tool(
    name="nova_shard_list",
    annotations={
        "title": "List All NOVA Shards",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def nova_shard_list() -> str:
    """List all shards with status summaries."""
    index = update_index()

    shards = []
    for shard_id, entry in index.items():
        shards.append({
            "shard_id": shard_id,
            "guiding_question": entry.get("guiding_question", ""),
            "tags": entry.get("tags", []),
            "theme": entry.get("meta", {}).get("theme", ""),
            "intent": entry.get("meta", {}).get("intent", ""),
        })

    return json.dumps({
        "total": len(shards),
        "shards": shards
    }, indent=2)


@mcp.tool(
    name="nova_shard_merge",
    annotations={
        "title": "Merge Shards into Meta-Shard",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False
    }
)
async def nova_shard_merge(params: ShardMergeInput) -> str:
    """Merge multiple shards into a single meta-shard."""
    merged_history = []
    source_questions = []

    for sid in params.shard_ids:
        try:
            data, _ = load_shard(sid)
            merged_history.extend(data.get("conversation_history", []))
            source_questions.append(f"{sid}: {data.get('guiding_question', '')}")
        except FileNotFoundError:
            return json.dumps({"status": "error", "message": f"Shard '{sid}' not found."}, indent=2)

    # Sort merged history by timestamp
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
            "merged_from": params.shard_ids,
            "source_questions": source_questions
        }
    }

    save_shard(filepath, meta_shard)

    if params.archive_originals:
        for sid in params.shard_ids:
            try:
                data, fp = load_shard(sid)
                data.setdefault("meta_tags", {})["intent"] = "archived"
                data["meta_tags"]["archived_at"] = datetime.now().isoformat()
                save_shard(fp, data)
            except FileNotFoundError:
                pass

    update_index()

    return json.dumps({
        "status": "merged",
        "new_shard_id": new_id,
        "sources": params.shard_ids,
        "total_entries": len(merged_history),
        "originals_archived": params.archive_originals
    }, indent=2)


@mcp.tool(
    name="nova_shard_archive",
    annotations={
        "title": "Archive a Shard",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
    }
)
async def nova_shard_archive(params: ShardArchiveInput) -> str:
    """Mark a shard as archived. Nothing is deleted — memory decays through deprioritization."""
    try:
        data, filepath = load_shard(params.shard_id)
    except FileNotFoundError:
        return json.dumps({"status": "error", "message": f"Shard '{params.shard_id}' not found."}, indent=2)

    data.setdefault("meta_tags", {})["intent"] = "archived"
    data["meta_tags"]["archived_at"] = datetime.now().isoformat()
    save_shard(filepath, data)

    return json.dumps({
        "status": "archived",
        "shard_id": params.shard_id,
        "guiding_question": data.get("guiding_question", "")
    }, indent=2)


# === MCP Resources ===

@mcp.resource("nova://skill")
async def nova_skill() -> str:
    """Return the NOVA SKILL.md — operating instructions for the cognitive architecture."""
    skill_path = Path(__file__).parent / "SKILL.md"
    if skill_path.exists():
        return skill_path.read_text(encoding="utf-8")
    return "SKILL.md not found. Operating without cognitive architecture instructions."


@mcp.resource("nova://index")
async def nova_index() -> str:
    """Return the current shard index."""
    index = update_index()
    return json.dumps(index, indent=2)


# === Entry Point ===

if __name__ == "__main__":
    mcp.run()
