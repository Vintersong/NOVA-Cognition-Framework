"""
forgemaster_tools.py — Forgemaster sprint orchestration and cache prewarming.

Two handlers: ``nova_forgemaster_sprint`` (full 4-turn pipeline) and
``nova_cache_prewarm`` (Anthropic prompt-cache primer). Registered via
``register_forgemaster_tools(mcp, ctx)``.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from forgemaster_runtime import ForgemasterRuntime
from gate_helpers import gate_check, log_executed, permission_error
from schemas import CachePrewarmInput, ForgemasterSprintInput
from tool_registry import nova_tool
from usage import log_operation

if TYPE_CHECKING:
    from server_context import ServerContext


def register_forgemaster_tools(mcp, ctx: "ServerContext") -> None:

    @nova_tool(mcp, name="nova_forgemaster_sprint")
    async def nova_forgemaster_sprint(params: ForgemasterSprintInput) -> str:
        """
        Run a full Forgemaster sprint: orchestrator → planner → implementer → reviewer.
        Loads optional shards into context, executes the 4-turn pipeline, flushes the
        session, and returns a sprint summary.

        ``shard_ids`` is an optional comma-separated list of shard IDs to load into
        context before the sprint begins (same pattern as nova_shard_interact).
        """
        if ctx.permission_context.blocks("nova_forgemaster_sprint"):
            return permission_error("nova_forgemaster_sprint")
        gate_err, request_id = await gate_check(ctx, "nova_forgemaster_sprint", params.sprint_id)
        if gate_err:
            return gate_err

        shard_id_list: list[str] = (
            [s.strip() for s in params.shard_ids.split(",") if s.strip()]
            if params.shard_ids
            else []
        )

        runtime = ForgemasterRuntime(
            ctx.session_store,
            ctx.permission_context,
            gate=ctx.capability_gate,
            audit_log=ctx.skill_audit_log,
        )
        op_ok = False
        try:
            try:
                summary = runtime.run_sprint(
                    params.sprint_id,
                    params.design_doc,
                    shard_id_list,
                    cached_system=params.cached_system,
                    task_type=params.task_type,
                    runtime_class=params.runtime_class,
                )
            except Exception as exc:
                return json.dumps({"error": str(exc)}, indent=2)

            log_operation(
                "nova_forgemaster_sprint",
                shard_id_list,
                {
                    "sprint_id": params.sprint_id,
                    "task_type": params.task_type,
                    "runtime_class": params.runtime_class,
                },
            )
            op_ok = True
            return json.dumps(summary, indent=2)
        finally:
            log_executed(ctx, request_id, "nova_forgemaster_sprint", params.sprint_id, op_ok)

    @nova_tool(mcp, name="nova_cache_prewarm")
    async def nova_cache_prewarm(params: CachePrewarmInput) -> str:
        """
        Pre-warm the Anthropic prompt cache with a summary context built from the
        top-N highest-confidence shards. Returns the system prompt string that must
        be passed (with cache_control) in every subsequent API call to get cache
        reads instead of writes.

        Call this once at the start of a Forgemaster sprint or any multi-turn
        session where the same shard context will appear across several API calls.
        The prewarm fires a max_tokens=0 request — no output generated, cache
        entry written. Subsequent calls with the same system string pay ~0.1× cost.
        """
        if ctx.permission_context.blocks("nova_cache_prewarm"):
            return permission_error("nova_cache_prewarm")

        from recall import prewarm_session_context
        from config import MUNINN_MODEL

        model = params.model or MUNINN_MODEL

        try:
            result = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: prewarm_session_context(
                    model=model,
                    top_n=params.top_n,
                    project_context=params.project_context,
                    min_confidence=params.min_confidence,
                ),
            )
        except Exception as exc:
            return json.dumps({"error": str(exc)}, indent=2)

        log_operation("nova_cache_prewarm", result.get("shard_ids", []), {
            "model": model,
            "top_n": params.top_n,
            "cache_write_tokens": result.get("cache_write_tokens", 0),
            "skipped": result.get("skipped", False),
        })

        output = {
            "status": "skipped" if result["skipped"] else "ok",
            "skip_reason": result.get("skip_reason", ""),
            "shard_count": len(result.get("shard_ids", [])),
            "shard_ids": result.get("shard_ids", []),
            "cache_write_tokens": result.get("cache_write_tokens", 0),
            "model": model,
            "note": (
                "" if result["skipped"] else
                "Pass system_prompt with cache_control on every subsequent call to get cache reads."
            ),
            "system_prompt": result.get("system_prompt", ""),
        }
        return json.dumps(output, indent=2)
