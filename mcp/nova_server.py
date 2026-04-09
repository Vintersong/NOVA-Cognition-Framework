"""
nova_server.py — NOVA MCP Server

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

Tools (18 total):
  nova_shard_interact   — load shards into context
  nova_shard_create     — create new shard (+ post-write hook)
  nova_shard_update     — append to shard (+ post-write hook + auto-compact)
  nova_shard_search     — search with confidence weighting
    nova_shard_index      — compact browse index, metadata only
    nova_shard_summary    — compact browse index with short synopsis
    nova_shard_list       — full raw dump fallback for legacy/admin use
  nova_shard_get        — read full raw shard content, no side effects
  nova_shard_merge      — merge shards into meta-shard
  nova_shard_archive    — soft-delete (sets intent=archived)
  nova_shard_forget     — hard soft-delete with provenance log
  nova_shard_consolidate — run decay + compact + merge suggestion cycle
  nova_graph_query      — query inter-shard knowledge graph
  nova_graph_relate     — manually add directed relation between shards
  nova_session_flush    — persist active sprint session to disk
  nova_session_load     — restore stored session to memory
  nova_session_list     — list all stored session IDs
  nova_forgemaster_sprint — full 4-turn sprint pipeline
"""

import asyncio
import json
import os
import sys as _sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP
from config import (
    SHARD_DIR, INDEX_FILE, GRAPH_FILE, USAGE_LOG_FILE,
    SESSION_STORE_DIR, MAX_FRAGMENTS,
    COMPACT_THRESHOLD, COMPACT_KEEP_RECENT,
    DECAY_RATE, DECAY_INTERVAL_DAYS, MERGE_SIMILARITY_THRESHOLD,
    HUGINN_CONFIDENCE_THRESHOLD, NOTT_COUNT_THRESHOLD,
)
from schemas import (
    ShardInteractInput, ShardCreateInput, ShardUpdateInput, ShardSearchInput,
    ShardListInput, ShardIndexInput, ShardMergeInput, ShardArchiveInput, ShardForgetInput, ShardGetInput,
    ShardConsolidateInput, GraphQueryInput, GraphRelationInput,
    SessionFlushInput, SessionLoadInput, SessionListInput,
    ForgemasterSprintInput,
)
from store import (
    sanitize_filename, get_unique_filename,
    load_shard, save_shard, update_shard_usage,
    load_index, save_index, classify_tags, update_index, patch_index_entry,
    guess_relevant_shards, collect_browse_rows, filter_sort_paginate_rows,
    group_rows_by_theme, refresh_summary_index_entry, rebuild_summary_indexes,
    extract_fragments,
)
from graph import (
    load_graph, save_graph,
    add_shard_to_graph, add_relation,
    query_graph, query_graph_transitive,
)
from maintenance import (
    get_confidence, apply_confidence_decay, confidence_weighted_score,
    maybe_compact_shard, cosine_similarity, find_merge_candidates,
)
from usage import log_operation
from nova_embeddings_local import enrich_shard, prewarm_embedding_model
from permissions import ToolPermissionContext
from models import UsageSummary
from session_store import SessionStore, NovaSession
from forgemaster_runtime import ForgemasterRuntime
from ravens import Huginn, Muninn
from nott import Nott, NottTrigger
from hooks import NovaHookRegistry, NovaHookEvent

_sys.path.insert(0, os.path.join(os.path.dirname(__file__), "Gemini"))
from gemini_mcp import register_gemini_tools

# Bootstrap
os.makedirs(SHARD_DIR, exist_ok=True)
prewarm_embedding_model()  # start loading embedding weights in background immediately

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
_session_store: SessionStore = SessionStore(SESSION_STORE_DIR)

# === Norse Pantheon — agent singletons ===
# Instantiated after env vars are resolved. Function references injected into
# NÓTT after the utility functions are defined below.
_huginn: Huginn          # bound after env setup
_muninn: Muninn          # bound after env setup
_nott: Nott              # bound after utility functions are defined
_hooks: NovaHookRegistry # bound after nott is initialized

mcp = FastMCP("nova_mcp_v2")
register_gemini_tools(mcp)

# ═══════════════════════════════════════════════════════════
# PERMISSION HELPERS
# ═══════════════════════════════════════════════════════════

_ALL_TOOL_NAMES: tuple[str, ...] = (
    "nova_shard_interact",
    "nova_shard_create",
    "nova_shard_update",
    "nova_shard_search",
    "nova_shard_index",
    "nova_shard_summary",
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
# NORSE PANTHEON — AGENT INITIALISATION
# All utility functions above must be defined before this block.
# NÓTT receives function references to avoid circular imports.
# ═══════════════════════════════════════════════════════════

_huginn = Huginn(
    shard_dir=SHARD_DIR,
    usage_log_file=USAGE_LOG_FILE,
    confidence_threshold=HUGINN_CONFIDENCE_THRESHOLD,
)

_muninn = Muninn(
    shard_dir=SHARD_DIR,
    usage_log_file=USAGE_LOG_FILE,
)

_nott = Nott(
    shard_dir=SHARD_DIR,
    graph_file=GRAPH_FILE,
    usage_log_file=USAGE_LOG_FILE,
    load_index_fn=load_index,
    update_index_fn=update_index,
    load_shard_fn=load_shard,
    save_shard_fn=save_shard,
    decay_fn=apply_confidence_decay,
    compact_fn=maybe_compact_shard,
    merge_fn=find_merge_candidates,
    load_graph_fn=load_graph,
    save_graph_fn=save_graph,
)

# ── Hook registry — event-driven dispatch (replaces bespoke create_task calls) ──
_hooks = NovaHookRegistry()
_hooks.register(NovaHookEvent.SESSION_START,
                lambda **_kw: _nott.run(NottTrigger.SESSION_START))
_hooks.register(NovaHookEvent.POST_SPRINT,
                lambda **_kw: _nott.run(NottTrigger.POST_SPRINT))
_hooks.register(NovaHookEvent.COUNT_THRESHOLD,
                lambda **_kw: _nott.run(NottTrigger.COUNT_THRESHOLD))


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
    huginn_confidence: float = 0.0
    muninn_used: bool = False

    if not shard_ids and params.auto_select:
        inferred = True
        index = load_index() or update_index()
        # ━━ HUGINN — fast first pass ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        huginn_result = await _huginn.retrieve(params.message, index)
        huginn_confidence = huginn_result.max_confidence
        retrieval = huginn_result
        # ━━ MUNINN — deep pass if HUGINN not confident ━━━━━━━━━━━━━━━━━━━━━━
        if not huginn_result.is_confident(_huginn.confidence_threshold):
            retrieval = await _muninn.rerank(params.message, huginn_result, index)
            muninn_used = True
        shard_ids = retrieval.shard_ids or []
        # ━━ NÓTT — lightweight session-start decay (fire-and-forget) ━━━━━━━━
        _hooks.emit(NovaHookEvent.SESSION_START)

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
        "huginn_confidence": round(huginn_confidence, 4),
        "muninn_used": muninn_used,
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

    # Post-write enrichment hook — runs in thread pool to avoid blocking the event loop
    await asyncio.get_running_loop().run_in_executor(None, enrich_shard, shard_id, shard_data)

    save_shard(filepath, shard_data)
    patch_index_entry(shard_id, shard_data)
    refresh_summary_index_entry(shard_id, shard_data, generate_missing=True)

    # Register in knowledge graph
    add_shard_to_graph(shard_id, shard_data)

    # Wire up relations to related shards
    for related_id in ([s.strip() for s in params.related_shards.split(",") if s.strip()] if params.related_shards else []):
        add_relation(shard_id, related_id, params.relation_type)

    # Check for merge candidates
    index = load_index()
    merge_candidates = find_merge_candidates(shard_id, shard_data, index)

    # NÓTT — count-threshold check: fire-and-forget if store is getting large
    if len(index) >= NOTT_COUNT_THRESHOLD:
        _hooks.emit(NovaHookEvent.COUNT_THRESHOLD)

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

    # NÓTT owns compaction — fire-and-forget post-sprint cycle
    # (replaces inline maybe_compact_shard call)
    _hooks.emit(NovaHookEvent.POST_SPRINT)

    # Post-write enrichment hook — runs in thread pool to avoid blocking the event loop
    await asyncio.get_running_loop().run_in_executor(None, enrich_shard, params.shard_id, data)

    save_shard(filepath, data)
    patch_index_entry(params.shard_id, data)
    refresh_summary_index_entry(params.shard_id, data, generate_missing=True)

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
        "nott_scheduled": True,
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

    # Run HUGINN → MUNINN pipeline to get semantic retrieval metadata
    huginn_result = await _huginn.retrieve(params.query, index, params.top_n)
    if not huginn_result.is_confident(_huginn.confidence_threshold):
        final_retrieval = await _muninn.rerank(params.query, huginn_result, index, params.top_n)
        muninn_fired = True
    else:
        final_retrieval = huginn_result
        muninn_fired = False

    log_operation("nova_shard_search", [], {"query": params.query})

    return json.dumps({
        "query": params.query,
        "results": results[:params.top_n],
        "total_searched": len(index),
        "huginn_confidence": round(huginn_result.max_confidence, 4),
        "muninn_used": muninn_fired,
        "huginn_ranking": final_retrieval.shard_ids,
    }, indent=2)


@mcp.tool(name="nova_shard_index")
async def nova_shard_index(params: ShardIndexInput) -> str:
    """Browse shards using compact metadata rows without loading conversation bodies."""
    if _permission_context.blocks("nova_shard_index"):
        return _permission_error("nova_shard_index")

    rebuild_summary_indexes(generate_missing=False)
    rows = collect_browse_rows(include_synopsis=False)
    page_rows, total = filter_sort_paginate_rows(
        rows,
        filter_tag=params.filter_tag,
        min_confidence=params.min_confidence,
        sort=params.sort,
        sort_order=params.sort_order,
        page=params.page,
        per_page=params.per_page,
    )

    payload = {
        "_v": 3,
        "tool": "nova_shard_index",
        "total": total,
        "page": params.page,
        "per_page": params.per_page,
        "returned": len(page_rows),
        "sort": params.sort,
        "sort_order": params.sort_order,
    }
    if params.group_by_theme:
        payload["themes"] = group_rows_by_theme(page_rows)
    else:
        payload["shards"] = page_rows
    return json.dumps(payload, indent=2)


@mcp.tool(name="nova_shard_summary")
async def nova_shard_summary(params: ShardIndexInput) -> str:
    """Browse shards with compact metadata rows plus a short synopsis per shard."""
    if _permission_context.blocks("nova_shard_summary"):
        return _permission_error("nova_shard_summary")

    rebuild_summary_indexes(generate_missing=False)
    rows = collect_browse_rows(include_synopsis=True)
    page_rows, total = filter_sort_paginate_rows(
        rows,
        filter_tag=params.filter_tag,
        min_confidence=params.min_confidence,
        sort=params.sort,
        sort_order=params.sort_order,
        page=params.page,
        per_page=params.per_page,
    )

    payload = {
        "_v": 3,
        "tool": "nova_shard_summary",
        "total": total,
        "page": params.page,
        "per_page": params.per_page,
        "returned": len(page_rows),
        "sort": params.sort,
        "sort_order": params.sort_order,
    }
    if params.group_by_theme:
        payload["themes"] = group_rows_by_theme(page_rows)
    else:
        payload["shards"] = page_rows
    return json.dumps(payload, indent=2)


@mcp.tool(name="nova_shard_list")
async def nova_shard_list(params: ShardListInput) -> str:
    """Return a legacy full shard dump. Prefer nova_shard_index or nova_shard_summary for browsing."""
    if _permission_context.blocks("nova_shard_list"):
        return _permission_error("nova_shard_list")

    rebuild_summary_indexes(generate_missing=False)
    rows = collect_browse_rows(include_synopsis=False)
    page_number = (params.offset // params.limit) + 1
    page_rows, total = filter_sort_paginate_rows(
        rows,
        filter_tag=params.tag_filter,
        min_confidence=None,
        sort="confidence",
        sort_order="desc",
        page=page_number,
        per_page=params.limit,
    )

    shards = []
    for row in page_rows:
        data, _ = load_shard(row["id"])
        shards.append(data)

    return json.dumps({
        "_v": 3,
        "deprecated": True,
        "mode": params.mode,
        "message": "Use nova_shard_index for browse and nova_shard_summary for pre-commit context.",
        "total": total,
        "offset": params.offset,
        "limit": params.limit,
        "returned": len(shards),
        "shards": shards,
    }, indent=2)


@mcp.tool(name="nova_shard_get")
async def nova_shard_get(params: ShardGetInput) -> str:
    """Read the full raw content of a shard from disk. Read-only, no side effects."""
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

    return json.dumps(data, indent=2)


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

    # Runs in thread pool to avoid blocking the event loop
    await asyncio.get_running_loop().run_in_executor(None, enrich_shard, new_id, meta_shard)
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
    Run the full maintenance cycle via NÓTT (Goddess of Night):
    1. Apply confidence decay to all shards not accessed in DECAY_INTERVAL_DAYS
    2. Auto-compact any shards exceeding COMPACT_THRESHOLD turns
    3. Surface merge suggestions for high-similarity pairs
    4. Sync knowledge graph entity confidence values
    5. Return a summary of what changed.

    This tool is for explicit manual invocation. Automated NÓTT cycles
    also run non-blocking on nova_shard_update (POST_SPRINT) and
    nova_shard_interact (SESSION_START).
    """
    if _permission_context.blocks("nova_shard_consolidate"):
        return _permission_error("nova_shard_consolidate")

    # Awaited — user explicitly requested this, blocking is acceptable
    report = await _nott.run(NottTrigger.SCHEDULED, dry_run=params.dry_run)

    log_operation("nova_shard_consolidate", [], {
        "trigger": "manual",
        "decayed": len(report.decayed_shards),
        "compacted": len(report.compacted_shards),
        "merge_suggestions": len(report.merge_suggestions),
        "dry_run": params.dry_run,
    })

    return json.dumps(report.to_dict(), indent=2)


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