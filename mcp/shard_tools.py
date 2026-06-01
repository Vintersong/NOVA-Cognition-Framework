"""
shard_tools.py — NOVA shard CRUD, browse, and lifecycle tools.

Fifteen handlers covering shard create/read/update/search/index/summary/list/
get/get_full/merge/archive/forget/consolidate plus state-query and obsidian
export. Registered via ``register_shard_tools(mcp, ctx)``.

The handler shapes match what previously lived in nova_server.py; only the
glue (permission context, gate, audit log, ravens, hook bus, lock map) is
read through the explicit ``ctx`` argument instead of module globals.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
from copy import deepcopy
from datetime import datetime
from typing import TYPE_CHECKING

from access_log import log_shard_access
from config import (
    MAX_FRAGMENTS,
    NOTT_COUNT_THRESHOLD,
    QUARANTINE_HOURS,
    SHARD_DIR,
)
from facts import search_facts
from gate_helpers import gate_check, log_executed, permission_error
from graph import (
    add_corroborated_by,
    add_relation,
    add_shard_to_graph,
    add_supersedes,
    load_graph,
)
from hooks import NovaHookEvent
from maintenance import confidence_weighted_score
from nott import NottTrigger
from nova_embeddings_local import enrich_shard
import provenance
from reject import RejectCode, reject_payload, shard_not_found
from schemas import (
    ObsidianExportInput,
    ShardArchiveInput,
    ShardConsolidateInput,
    ShardCreateInput,
    ShardForgetInput,
    ShardGetFullInput,
    ShardGetInput,
    ShardIndexInput,
    ShardInteractInput,
    ShardListInput,
    ShardMergeInput,
    ShardSearchInput,
    ShardStateQueryInput,
    ShardUpdateInput,
    ShardValidateInput,
)
from server_context import _update_graph_entity_confidence
from store import (
    classify_tags,
    collect_browse_rows,
    extract_fragments,
    filter_sort_paginate_rows,
    get_unique_filename,
    group_rows_by_theme,
    load_index,
    load_shard,
    mutate_shard,
    mutate_shard_fields,
    patch_index_entry,
    rebuild_summary_indexes,
    refresh_summary_index_entry,
    sanitize_filename,
    save_shard,
    update_index,
    update_shard_usage,
)
from tool_registry import nova_tool
from usage import log_operation

if TYPE_CHECKING:
    from server_context import ServerContext


# Cached report from the most recent NÓTT cycle triggered via
# nova_shard_consolidate. Module-level so the dry_run lookup persists across
# tool invocations; module-private since nothing else needs it.
_last_consolidation_report: dict | None = None


def _local_keyword_search(query: str, include_low_confidence: bool) -> tuple[dict, list]:
    """Keyword scoring over the shard index — runs in executor to keep event loop free."""
    index = load_index() or update_index()
    query_tokens = set(query.lower().split())
    results = []

    for shard_id, entry in index.items():
        tags = entry.get("tags", [])
        if "archived" in tags or "forgotten" in tags:
            continue
        if not include_low_confidence and "low_confidence" in tags:
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
    return index, results


def register_shard_tools(mcp, ctx: "ServerContext") -> dict:
    """Register all shard tools onto *mcp*.

    Returns a ``{name: handler}`` dict so callers (and the test suite) can
    invoke handlers directly without going through the MCP transport.
    """

    @nova_tool(mcp, name="nova_shard_interact")
    async def nova_shard_interact(params: ShardInteractInput) -> str:
        """Load shards into context for synthesis. Auto-selects relevant shards if none specified. Confidence-weighted."""
        if ctx.permission_context.blocks("nova_shard_interact"):
            return permission_error("nova_shard_interact")

        shard_ids = [s.strip() for s in params.shard_ids.split(",") if s.strip()] if params.shard_ids else []
        inferred = False
        huginn_confidence: float = 0.0
        muninn_used: bool = False

        facts_hits: list[dict] = []
        if not shard_ids and params.auto_select:
            inferred = True
            loop_interact = asyncio.get_running_loop()
            index = await loop_interact.run_in_executor(
                None, lambda: load_index() or update_index()
            )
            try:
                facts_hits = search_facts(params.message, confidence=1, limit=5)
            except Exception:
                facts_hits = []
            try:
                huginn_result = await asyncio.wait_for(
                    ctx.huginn.retrieve(params.message, index),
                    timeout=20.0,
                )
                huginn_confidence = huginn_result.max_confidence
                retrieval = huginn_result
                if not huginn_result.is_confident(ctx.huginn.confidence_threshold):
                    try:
                        retrieval = await asyncio.wait_for(
                            ctx.muninn.rerank(params.message, huginn_result, index),
                            timeout=20.0,
                        )
                        muninn_used = True
                    except asyncio.TimeoutError:
                        retrieval = huginn_result
                shard_ids = retrieval.shard_ids or []
            except asyncio.TimeoutError:
                shard_ids = []
            ctx.hooks.emit(NovaHookEvent.SESSION_START)

        if not shard_ids:
            return json.dumps({
                "status": "no_shards_found",
                "message": "No shards matched the query.",
                "suggestion": "Use nova_shard_search to find relevant shards, or nova_shard_create to start a new one."
            }, indent=2)

        loaded = []
        errors = []

        _loop_for_load = asyncio.get_running_loop()
        for sid in shard_ids:
            try:
                data, filepath = await _loop_for_load.run_in_executor(None, load_shard, sid)
                update_shard_usage(data)
                meta = data.setdefault("meta_tags", {})
                await _loop_for_load.run_in_executor(None, save_shard, filepath, data)

                fragments = extract_fragments(data, sid, max_turns=MAX_FRAGMENTS)

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
            "facts": facts_hits,
            "shards": loaded,
            "errors": errors
        }
        response_str = json.dumps(response_payload, indent=2)

        ctx.session_usage = ctx.session_usage.add_turn(params.message, response_str)

        _session_id = params.session_id
        _active_session = None
        if _session_id:
            existing = ctx.session_store.get(_session_id)
            _active_session = existing if existing is not None else ctx.session_store.create(_session_id)
            _active_session = _active_session.add_message("user", params.message)
            _active_session = _active_session.add_message("assistant", response_str)
            ctx.session_store.update(_active_session)

        log_entry: dict = {
            "session_input_tokens": ctx.session_usage.input_tokens,
            "session_output_tokens": ctx.session_usage.output_tokens,
            "session_total_tokens": ctx.session_usage.total_tokens,
        }
        if _session_id and _active_session is not None:
            log_entry["session_id"] = _session_id
            log_entry["session_message_count"] = len(_active_session.messages)

        log_operation("nova_shard_interact", shard_ids, log_entry)

        for sid in shard_ids:
            log_shard_access(sid, "nova_shard_interact")

        return response_str

    @nova_tool(mcp, name="nova_shard_create")
    async def nova_shard_create(params: ShardCreateInput) -> str:
        """Create a new shard. Triggers post-write enrichment hook and registers in knowledge graph."""
        if ctx.permission_context.blocks("nova_shard_create"):
            return permission_error("nova_shard_create")
        base_name = sanitize_filename(f"{params.theme}_{params.intent}")
        filename = get_unique_filename(base_name)
        filepath = os.path.join(SHARD_DIR, filename)
        shard_id = filename.replace(".json", "")

        from datetime import timedelta
        from timeutils import now_utc
        quarantine_until = None
        if params.source == "session_extracted":
            quarantine_until = (now_utc() + timedelta(hours=QUARANTINE_HOURS)).isoformat()

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
                "enrichment_status": "pending",
                "source": params.source,
                "quarantine_until": quarantine_until,
                "project_context": params.project_context,
                "validity_window": (
                    {"start": params.validity_start, "end": params.validity_end}
                    if params.validity_start or params.validity_end else None
                ),
                "superseded_by": None,
                "epistemic_provenance": provenance.build_initial(
                    params.source,
                    source_type=params.prov_source_type,
                    validator=params.prov_validator,
                    mechanism=params.prov_mechanism,
                ),
            }
        }

        if params.initial_message:
            shard_data["conversation_history"].append({
                "timestamp": datetime.now().isoformat(),
                "user": params.initial_message,
                "ai": ""
            })

        shard_lock = ctx.get_shard_lock(shard_id)
        loop = asyncio.get_running_loop()
        async with shard_lock:
            await loop.run_in_executor(None, save_shard, filepath, shard_data)
            await loop.run_in_executor(None, patch_index_entry, shard_id, shard_data)

        add_shard_to_graph(shard_id, shard_data)

        new_source = shard_data.get("meta_tags", {}).get("source", "agent_inference")
        _credible_sources = {"user_input", "external_doc"}
        for related_id in ([s.strip() for s in params.related_shards.split(",") if s.strip()] if params.related_shards else []):
            if params.relation_type == "supersedes":
                add_supersedes(shard_id, related_id, reason=params.reason)
            else:
                add_relation(shard_id, related_id, params.relation_type)
            if params.relation_type == "contradicts" and new_source in _credible_sources:
                try:
                    existing, existing_filepath = await loop.run_in_executor(None, load_shard, related_id)
                    existing_conf = existing.get("meta_tags", {}).get("confidence", 1.0)
                    new_conf = shard_data.get("meta_tags", {}).get("confidence", 1.0)
                    if new_conf >= existing_conf:
                        add_supersedes(
                            shard_id, related_id,
                            reason=f"New {new_source} shard (conf={new_conf}) contradicts and supersedes existing (conf={existing_conf})",
                        )
                        existing.setdefault("meta_tags", {})["superseded_by"] = shard_id
                        await loop.run_in_executor(None, save_shard, existing_filepath, existing)
                        await loop.run_in_executor(None, patch_index_entry, related_id, existing)
                except Exception:
                    pass

        loop = asyncio.get_running_loop()

        async def _background_enrich_and_persist() -> None:
            await loop.run_in_executor(None, enrich_shard, shard_id, shard_data)
            async with shard_lock:
                await loop.run_in_executor(None, save_shard, filepath, shard_data)
                await loop.run_in_executor(None, patch_index_entry, shard_id, shard_data)

        asyncio.create_task(_background_enrich_and_persist())

        async def _background_summary() -> None:
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(None, refresh_summary_index_entry, shard_id, shard_data, True),
                    timeout=20.0,
                )
            except Exception:
                pass

        asyncio.create_task(_background_summary())

        index = await loop.run_in_executor(None, load_index)
        if len(index) >= NOTT_COUNT_THRESHOLD:
            ctx.hooks.emit(NovaHookEvent.COUNT_THRESHOLD)

        log_operation("nova_shard_create", [shard_id])

        return json.dumps({
            "status": "created",
            "shard_id": shard_id,
            "guiding_question": params.guiding_question,
            "enrichment_status": "pending",
        }, indent=2)

    @nova_tool(mcp, name="nova_shard_update")
    async def nova_shard_update(params: ShardUpdateInput) -> str:
        """Append to a shard. Triggers post-write enrichment hook and auto-compaction if threshold exceeded."""
        if ctx.permission_context.blocks("nova_shard_update"):
            return permission_error("nova_shard_update")

        loop = asyncio.get_running_loop()
        shard_lock = ctx.get_shard_lock(params.shard_id)

        turn = {
            "timestamp": datetime.now().isoformat(),
            "user": params.user_message,
            "ai": params.ai_response,
        }

        # The append must land on the *current* on-disk shard, not a snapshot
        # loaded before a concurrent NÓTT pass ran. mutate_shard re-reads under
        # the shard FileLock and applies the append there, so a confidence/tag
        # change written by NÓTT in between is preserved instead of clobbered.
        holder: dict = {}

        def _append(fresh: dict) -> None:
            fresh.setdefault("conversation_history", []).append(turn)
            update_shard_usage(fresh)
            fresh.setdefault("meta_tags", {})["enrichment_status"] = "pending"
            holder["data"] = fresh

        async with shard_lock:
            written = await loop.run_in_executor(
                None, lambda: mutate_shard(params.shard_id, _append)
            )
            if not written:
                return shard_not_found(params.shard_id)
            data = holder["data"]
            await loop.run_in_executor(None, patch_index_entry, params.shard_id, data)

        ctx.hooks.emit(NovaHookEvent.POST_SPRINT)

        await loop.run_in_executor(None, lambda: _update_graph_entity_confidence(params.shard_id, data))

        loop = asyncio.get_running_loop()

        async def _background_enrich_and_persist() -> None:
            # Enrich a snapshot, then field-merge only the enrichment delta so a
            # turn appended (or NÓTT field write) between now and persist survives.
            before = deepcopy(data)
            await loop.run_in_executor(None, enrich_shard, params.shard_id, data)
            async with shard_lock:
                await loop.run_in_executor(
                    None, lambda: mutate_shard_fields(params.shard_id, before, data)
                )
                await loop.run_in_executor(None, patch_index_entry, params.shard_id, data)

        asyncio.create_task(_background_enrich_and_persist())

        async def _background_summary_update() -> None:
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(None, refresh_summary_index_entry, params.shard_id, data, True),
                    timeout=20.0,
                )
            except Exception:
                pass

        asyncio.create_task(_background_summary_update())

        log_operation("nova_shard_update", [params.shard_id])

        return json.dumps({
            "status": "updated",
            "shard_id": params.shard_id,
            "total_entries": len(data["conversation_history"]),
            "nott_scheduled": True,
            "enrichment_status": "pending",
        }, indent=2)

    @nova_tool(mcp, name="nova_shard_validate")
    async def nova_shard_validate(params: ShardValidateInput) -> str:
        """Record an epistemic validation event on a shard.

        Sets the shard's authority chain (source_type, validator, mechanism),
        optionally raises confidence via the sanctioned corroboration path, and
        can mark the memory as superseded by a higher-authority source. Writes
        a corroborated_by graph edge when a positive delta is attributed to
        another shard. Mirrors nova_shard_update's atomic mutate-under-lock path.
        """
        if ctx.permission_context.blocks("nova_shard_validate"):
            return permission_error("nova_shard_validate")

        loop = asyncio.get_running_loop()
        shard_lock = ctx.get_shard_lock(params.shard_id)
        holder: dict = {}

        def _validate(fresh: dict) -> None:
            record = provenance.apply_validation_event(
                fresh,
                source_type=params.source_type,
                validator=params.validator,
                mechanism=params.mechanism,
                confidence_delta=params.confidence_delta,
                superseded=params.superseded,
                superseded_by=params.superseded_by,
            )
            holder["record"] = record
            holder["data"] = fresh

        async with shard_lock:
            try:
                written = await loop.run_in_executor(
                    None, lambda: mutate_shard(params.shard_id, _validate)
                )
            except ValueError as exc:
                return reject_payload(RejectCode.INVALID_INPUT, str(exc))
            if not written:
                return shard_not_found(params.shard_id)
            data = holder["data"]
            await loop.run_in_executor(None, patch_index_entry, params.shard_id, data)

        # If a positive delta was attributed to another shard, record the
        # corroborating edge so the authority chain stays queryable in the graph.
        if params.confidence_delta > 0 and params.validator:
            graph = await loop.run_in_executor(None, load_graph)
            if params.validator in graph.get("entities", {}):
                await loop.run_in_executor(
                    None, add_corroborated_by, params.shard_id, params.validator
                )

        log_operation("nova_shard_validate", [params.shard_id])

        return json.dumps({
            "status": "validated",
            "shard_id": params.shard_id,
            "confidence": data.get("meta_tags", {}).get("confidence"),
            "epistemic_provenance": holder.get("record"),
        }, indent=2)

    @nova_tool(mcp, name="nova_shard_search")
    async def nova_shard_search(params: ShardSearchInput) -> str:
        """Search shards with confidence weighting. High-confidence shards rank higher for same relevance score."""
        if ctx.permission_context.blocks("nova_shard_search"):
            return permission_error("nova_shard_search")

        loop = asyncio.get_running_loop()
        index, results = await loop.run_in_executor(
            None, _local_keyword_search, params.query, params.include_low_confidence
        )

        huginn_confidence = 0.0
        muninn_fired = False
        huginn_ranking: list[str] = []
        try:
            huginn_result = await asyncio.wait_for(
                ctx.huginn.retrieve(params.query, index, params.top_n),
                timeout=20.0,
            )
            huginn_confidence = huginn_result.max_confidence
            huginn_ranking = huginn_result.shard_ids
            if not huginn_result.is_confident(ctx.huginn.confidence_threshold):
                try:
                    final_retrieval = await asyncio.wait_for(
                        ctx.muninn.rerank(params.query, huginn_result, index, params.top_n),
                        timeout=20.0,
                    )
                    huginn_ranking = final_retrieval.shard_ids
                    muninn_fired = True
                except asyncio.TimeoutError:
                    pass
        except asyncio.TimeoutError:
            pass

        log_operation(
            "nova_shard_search",
            [],
            {
                "query_length": len(params.query),
                "query_sha256_16": hashlib.sha256(params.query.encode("utf-8")).hexdigest()[:16],
            },
        )

        returned_ids = [r["shard_id"] for r in results[:params.top_n]]
        for sid in returned_ids:
            log_shard_access(sid, "nova_shard_search")

        return json.dumps({
            "query": params.query,
            "results": results[:params.top_n],
            "total_searched": len(index),
            "huginn_confidence": round(huginn_confidence, 4),
            "muninn_used": muninn_fired,
            "huginn_ranking": huginn_ranking,
        }, indent=2)

    @nova_tool(mcp, name="nova_shard_query_state")
    async def nova_shard_query_state(params: ShardStateQueryInput) -> str:
        """Query the SQLite shard index by epistemic state vector.

        Uses the structured integer encoding (confidence × valence × arousal × epistemic)
        for fast single-predicate range queries — no full index scan.

        Examples:
          - stats_only=true              → state distribution across all shards
          - min_confidence=0.85, epistemic=2  → confirmed high-confidence shards
          - epistemic=0                  → all contradicted shards
          - valence_min=7                → positively-valenced shards
          - keyword="quarantine"         → filter by guiding_question/theme/intent
        """
        if ctx.permission_context.blocks("nova_shard_query_state"):
            return permission_error("nova_shard_query_state")

        from nova_shard_db import get_nova_shard_db, decode_state

        db = get_nova_shard_db()

        if params.stats_only:
            stats = db.stats()
            return json.dumps({"stats": stats}, indent=2)

        if params.keyword:
            rows = db.search(params.keyword, limit=params.limit)
            rows = [
                r for r in rows
                if params.min_confidence <= r["confidence"] <= params.max_confidence
                and (params.epistemic is None or r["epistemic"] == params.epistemic)
                and (params.valence_min is None or r["valence"] >= params.valence_min)
            ]
        else:
            rows = db.query_state_range(
                min_confidence=params.min_confidence,
                max_confidence=params.max_confidence,
                epistemic=params.epistemic,
                valence_min=params.valence_min,
                limit=params.limit,
            )

        results = [
            {
                "shard_id": r["id"],
                "guiding_question": r["guiding_question"],
                "confidence": r["confidence"],
                "state": decode_state(r["state"]),
                "provenance": r["provenance"],
                "theme": r["theme"],
                "quarantine_until": r["quarantine_until"],
            }
            for r in rows
        ]

        log_operation("nova_shard_query_state", [], {
            "min_confidence": params.min_confidence,
            "max_confidence": params.max_confidence,
            "epistemic": params.epistemic,
            "returned": len(results),
        })

        return json.dumps({
            "returned": len(results),
            "filters": {
                "min_confidence": params.min_confidence,
                "max_confidence": params.max_confidence,
                "epistemic": params.epistemic,
                "valence_min": params.valence_min,
                "keyword": params.keyword,
            },
            "results": results,
        }, indent=2)

    @nova_tool(mcp, name="nova_obsidian_export")
    async def nova_obsidian_export(params: ObsidianExportInput) -> str:
        """Export all shards to an Obsidian vault as Markdown files with YAML frontmatter
        and [[wikilink]] edges derived from the knowledge graph.

        Output dir: NOVA_OBSIDIAN_DIR env var (default: output/obsidian_vault/).
        Skips archived and forgotten shards. Writes _NOVA_INDEX.md at the vault root.
        """
        if ctx.permission_context.blocks("nova_obsidian_export"):
            return permission_error("nova_obsidian_export")

        from obsidian_export import export_shards, OBSIDIAN_DIR

        loop = asyncio.get_running_loop()
        index = await loop.run_in_executor(None, lambda: load_index() or update_index())
        graph = await loop.run_in_executor(None, load_graph)
        out_dir = params.out_dir.strip() or OBSIDIAN_DIR

        if params.dry_run:
            skippable = sum(
                1 for e in index.values()
                if "forgotten" in e.get("tags", []) or "archived" in e.get("tags", [])
            )
            return json.dumps({
                "dry_run": True,
                "would_export": len(index) - skippable,
                "would_skip": skippable,
                "out_dir": out_dir,
            }, indent=2)

        result = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: export_shards(index, load_shard, graph, out_dir),
        )

        log_operation("nova_obsidian_export", [], {
            "exported": result["exported"],
            "skipped": result["skipped"],
            "errors": len(result["errors"]),
            "out_dir": out_dir,
        })

        return json.dumps({
            "exported": result["exported"],
            "skipped": result["skipped"],
            "errors": result["errors"][:10],
            "out_dir": out_dir,
            "index_note": str(out_dir) + "/_NOVA_INDEX.md",
        }, indent=2)

    @nova_tool(mcp, name="nova_shard_index")
    async def nova_shard_index(params: ShardIndexInput) -> str:
        """Browse shards using compact metadata rows without loading conversation bodies."""
        if ctx.permission_context.blocks("nova_shard_index"):
            return permission_error("nova_shard_index")

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: rebuild_summary_indexes(generate_missing=False))
        rows = await loop.run_in_executor(None, lambda: collect_browse_rows(include_synopsis=False))
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

    @nova_tool(mcp, name="nova_shard_summary")
    async def nova_shard_summary(params: ShardIndexInput) -> str:
        """Browse shards with compact metadata rows plus a short synopsis per shard."""
        if ctx.permission_context.blocks("nova_shard_summary"):
            return permission_error("nova_shard_summary")

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: rebuild_summary_indexes(generate_missing=False))
        rows = await loop.run_in_executor(None, lambda: collect_browse_rows(include_synopsis=True))
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

    @nova_tool(mcp, name="nova_shard_list")
    async def nova_shard_list(params: ShardListInput) -> str:
        """Return a legacy full shard dump. Prefer nova_shard_index or nova_shard_summary for browsing."""
        if ctx.permission_context.blocks("nova_shard_list"):
            return permission_error("nova_shard_list")

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: rebuild_summary_indexes(generate_missing=False))
        rows = await loop.run_in_executor(None, lambda: collect_browse_rows(include_synopsis=False))
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

        def _load_all_shards(rows: list) -> list:
            return [load_shard(row["id"])[0] for row in rows]

        shards = await loop.run_in_executor(None, _load_all_shards, page_rows)

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

    @nova_tool(mcp, name="nova_shard_get")
    async def nova_shard_get(params: ShardGetInput) -> str:
        """Read the full raw content of a shard from disk. Read-only, no side effects."""
        if ctx.permission_context.blocks("nova_shard_get"):
            return permission_error("nova_shard_get")
        loop = asyncio.get_running_loop()
        try:
            data, _ = await loop.run_in_executor(None, load_shard, params.shard_id)
        except FileNotFoundError:
            return shard_not_found(params.shard_id)

        return json.dumps(data, indent=2)

    @nova_tool(mcp, name="nova_shard_get_full")
    async def nova_shard_get_full(params: ShardGetFullInput) -> str:
        """Cold-path full-body fetch. Returns conversation_history/turns payload without side effects. Use nova_shard_get for raw metadata."""
        if ctx.permission_context.blocks("nova_shard_get_full"):
            return permission_error("nova_shard_get_full")
        loop = asyncio.get_running_loop()
        try:
            data, _ = await loop.run_in_executor(None, load_shard, params.shard_id)
        except FileNotFoundError:
            return shard_not_found(params.shard_id)

        body = data.get("conversation_history") or data.get("turns") or []
        return json.dumps({
            "shard_id": params.shard_id,
            "guiding_question": data.get("guiding_question", ""),
            "source": data.get("meta_tags", {}).get("source", "agent_inference"),
            "summary": data.get("meta_tags", {}).get("summary", ""),
            "body": body,
        }, indent=2)

    @nova_tool(mcp, name="nova_shard_merge")
    async def nova_shard_merge(params: ShardMergeInput) -> str:
        """Merge multiple shards into a meta-shard. Updates knowledge graph relations."""
        if ctx.permission_context.blocks("nova_shard_merge"):
            return permission_error("nova_shard_merge")
        loop = asyncio.get_running_loop()
        merged_history = []
        source_questions = []
        shard_ids_list = [s.strip() for s in params.shard_ids.split(",") if s.strip()]

        for sid in shard_ids_list:
            try:
                data, _ = await loop.run_in_executor(None, load_shard, sid)
                merged_history.extend(data.get("conversation_history", []))
                source_questions.append(f"{sid}: {data.get('guiding_question', '')}")
            except FileNotFoundError:
                return shard_not_found(sid)

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

        meta_shard.setdefault("meta_tags", {})["enrichment_status"] = "pending"
        await loop.run_in_executor(None, save_shard, filepath, meta_shard)
        await loop.run_in_executor(None, patch_index_entry, new_id, meta_shard)
        add_shard_to_graph(new_id, meta_shard)

        if params.archive_originals:
            for sid in shard_ids_list:
                try:
                    data, fp = await loop.run_in_executor(None, load_shard, sid)
                    data.setdefault("meta_tags", {})["intent"] = "archived"
                    data["meta_tags"]["archived_at"] = datetime.now().isoformat()
                    await loop.run_in_executor(None, save_shard, fp, data)
                except FileNotFoundError:
                    pass

        loop = asyncio.get_running_loop()

        async def _background_enrich_and_persist() -> None:
            await loop.run_in_executor(None, enrich_shard, new_id, meta_shard)
            await loop.run_in_executor(None, save_shard, filepath, meta_shard)
            await loop.run_in_executor(None, patch_index_entry, new_id, meta_shard)

        asyncio.create_task(_background_enrich_and_persist())

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

    @nova_tool(mcp, name="nova_shard_archive")
    async def nova_shard_archive(params: ShardArchiveInput) -> str:
        """Soft-archive a shard. Excluded from search. Memory decays through deprioritization, not deletion."""
        if ctx.permission_context.blocks("nova_shard_archive"):
            return permission_error("nova_shard_archive")
        gate_err, request_id = await gate_check(ctx, "nova_shard_archive", params.shard_id)
        if gate_err:
            return gate_err
        op_ok = False
        loop = asyncio.get_running_loop()
        try:
            try:
                data, filepath = await loop.run_in_executor(None, load_shard, params.shard_id)
            except FileNotFoundError:
                return shard_not_found(params.shard_id)

            data.setdefault("meta_tags", {})["intent"] = "archived"
            data["meta_tags"]["archived_at"] = datetime.now().isoformat()
            await loop.run_in_executor(None, save_shard, filepath, data)
            await loop.run_in_executor(None, patch_index_entry, params.shard_id, data)
            op_ok = True

            return json.dumps({
                "status": "archived",
                "shard_id": params.shard_id,
                "guiding_question": data.get("guiding_question", "")
            }, indent=2)
        finally:
            log_executed(ctx, request_id, "nova_shard_archive", params.shard_id, op_ok)

    @nova_tool(mcp, name="nova_shard_forget")
    async def nova_shard_forget(params: ShardForgetInput) -> str:
        """
        Hard soft-delete with provenance log.
        Shard is marked as forgotten and removed from all search/interact results.
        Content preserved on disk for audit. Logged to usage file with reason.
        This is different from archive — forgotten shards are intentionally excluded,
        not just deprioritized.
        """
        if ctx.permission_context.blocks("nova_shard_forget"):
            return permission_error("nova_shard_forget")
        gate_err, request_id = await gate_check(ctx, "nova_shard_forget", params.shard_id)
        if gate_err:
            return gate_err
        op_ok = False
        loop = asyncio.get_running_loop()
        try:
            try:
                data, filepath = await loop.run_in_executor(None, load_shard, params.shard_id)
            except FileNotFoundError:
                return shard_not_found(params.shard_id)

            data.setdefault("meta_tags", {})["intent"] = "forgotten"
            data["meta_tags"]["forgotten_at"] = datetime.now().isoformat()
            data["meta_tags"]["forget_reason"] = params.reason
            data["meta_tags"]["confidence"] = 0.0
            await loop.run_in_executor(None, save_shard, filepath, data)
            await loop.run_in_executor(None, patch_index_entry, params.shard_id, data)

            log_operation("nova_shard_forget", [params.shard_id], {"reason": params.reason})
            op_ok = True

            return json.dumps({
                "status": "forgotten",
                "shard_id": params.shard_id,
                "reason": params.reason,
                "note": "Shard preserved on disk for audit. Excluded from all search and interact operations."
            }, indent=2)
        finally:
            log_executed(ctx, request_id, "nova_shard_forget", params.shard_id, op_ok)

    @nova_tool(mcp, name="nova_shard_consolidate")
    async def nova_shard_consolidate(params: ShardConsolidateInput) -> str:
        """
        Trigger a full NÓTT maintenance cycle (fire-and-forget).
        Returns immediately — NÓTT runs entirely in the background.

        Call with dry_run=true to get the last completed report without
        triggering a new cycle.

        NÓTT also runs automatically:
          - SESSION_START (decay only) on every nova_shard_interact
          - POST_SPRINT (decay + compact + merge + graph) on every nova_shard_update
          - COUNT_THRESHOLD when shard count exceeds the threshold
        You rarely need to call this manually.
        """
        global _last_consolidation_report

        if ctx.permission_context.blocks("nova_shard_consolidate"):
            return permission_error("nova_shard_consolidate")

        if params.dry_run:
            if _last_consolidation_report:
                return json.dumps({
                    "status": "last_report",
                    **_last_consolidation_report,
                }, indent=2)
            return json.dumps({"status": "no_report_yet", "hint": "Call with dry_run=false to trigger a cycle."}, indent=2)

        gate_err, request_id = await gate_check(ctx, "nova_shard_consolidate")
        if gate_err:
            return gate_err

        def _nott_thread() -> None:
            global _last_consolidation_report
            if not ctx._nott_lock.acquire(blocking=False):
                _last_consolidation_report = {"status": "skipped", "reason": "Another NÓTT cycle is already running."}
                return
            try:
                report = asyncio.run(ctx.nott.run(NottTrigger.SCHEDULED, dry_run=False))
                _last_consolidation_report = report.to_dict()
                log_operation("nova_shard_consolidate", [], {
                    "trigger": "manual",
                    "decayed": len(report.decayed_shards),
                    "compacted": len(report.compacted_shards),
                    "merge_suggestions": len(report.merge_suggestions),
                })
            except Exception as exc:
                _last_consolidation_report = {"status": "error", "error": str(exc)}
            finally:
                ctx._nott_lock.release()

        threading.Thread(target=_nott_thread, daemon=True).start()
        log_executed(ctx, request_id, "nova_shard_consolidate", None, True)

        return json.dumps({
            "status": "scheduled",
            "message": "NÓTT maintenance cycle started in background. Call with dry_run=true to check the last completed report.",
        }, indent=2)

    return {
        "nova_shard_interact": nova_shard_interact,
        "nova_shard_create": nova_shard_create,
        "nova_shard_update": nova_shard_update,
        "nova_shard_search": nova_shard_search,
        "nova_shard_query_state": nova_shard_query_state,
        "nova_obsidian_export": nova_obsidian_export,
        "nova_shard_index": nova_shard_index,
        "nova_shard_summary": nova_shard_summary,
        "nova_shard_list": nova_shard_list,
        "nova_shard_get": nova_shard_get,
        "nova_shard_get_full": nova_shard_get_full,
        "nova_shard_merge": nova_shard_merge,
        "nova_shard_archive": nova_shard_archive,
        "nova_shard_forget": nova_shard_forget,
        "nova_shard_consolidate": nova_shard_consolidate,
    }
