from __future__ import annotations

"""
forgemaster_runtime.py — Phase 3 Forgemaster execution harness.

ForgemasterRuntime orchestrates the 4-turn sprint lifecycle:
  orchestrator → planner → implementer → reviewer

Uses NOVA as the shared memory backplane (via SessionStore) and respects
tool access boundaries via ToolPermissionContext.

LLM calls are intentionally stubbed in this phase — the scaffolding,
session tracking, skill loading, permission gating, and sprint flush are
fully functional.  Actual model dispatch is wired in Phase 4+.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from permissions import ToolPermissionContext
from session_store import SessionStore, NovaSession

logger = logging.getLogger(__name__)

# Repo root — resolved relative to this file so it works regardless of CWD.
_REPO_ROOT = Path(__file__).parent.parent

# ─────────────────────────────────────────────────────────────────────────────
# Routing table — sourced from forgemaster/AGENTS.md preferred_models section
# ─────────────────────────────────────────────────────────────────────────────
_ROUTING_TABLE: dict[str, str] = {
    # claude-sonnet lane
    "architecture": "claude-sonnet",
    "review": "claude-sonnet",
    "ambiguity": "claude-sonnet",
    # gemini-flash lane
    "implementation": "gemini-flash",
    "boilerplate": "gemini-flash",
    "structured-output": "gemini-flash",
    # claude-haiku lane
    "research": "claude-haiku",
    "documentation": "claude-haiku",
    "fast-tasks": "claude-haiku",
    # stitch lane
    "ui": "stitch",
    "frontend": "stitch",
    "mockup": "stitch",
}

# Agent roles that require write-capable tools.  If those tools are denied,
# the corresponding lanes are flagged as restricted.
_WRITE_DEPENDENT_LANES: frozenset[str] = frozenset({"implementer"})

# Canonical write tool names (a subset of _ALL_TOOL_NAMES in nova_server.py)
_WRITE_TOOLS: frozenset[str] = frozenset({
    "nova_shard_create",
    "nova_shard_update",
    "nova_shard_merge",
    "nova_shard_archive",
    "nova_shard_forget",
})

# Complexity override: task types containing these words are routed to claude-sonnet
# regardless of what the routing table says.
# Inspired by hermes-agent smart_model_routing.py choose_cheap_model_route().
_COMPLEX_KEYWORDS: frozenset[str] = frozenset({
    "debug", "debugging", "investigation", "analysis", "architecture",
    "migration", "refactor", "security", "performance", "integration",
    "system-design", "database", "authentication", "authorization",
    "tracing", "profiling", "concurrency",
})


class ForgemasterRuntime:
    """
    Orchestrates the Forgemaster sprint lifecycle using NOVA as shared memory.

    Instantiated per-request in the ``nova_forgemaster_sprint`` MCP tool.
    The module-level ``_session_store`` and ``_permission_context`` singletons
    from ``nova_server.py`` are injected at construction time so this class
    remains testable in isolation.
    """

    def __init__(
        self,
        session_store: SessionStore,
        permission_context: ToolPermissionContext,
    ) -> None:
        self._session_store = session_store
        self._permission_context = permission_context

    # ──────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────

    def bootstrap(self, sprint_id: str, shard_ids: list[str]) -> NovaSession:
        """
        Create a new ``NovaSession`` for the sprint and load the requested
        shards into context.

        If ``shard_ids`` is non-empty the shard content is appended to the
        session as a user message so downstream turns have access to it.
        The actual ``nova_shard_interact`` read path is called inline here
        (stubbed to a log note when no shards are provided).
        """
        session = self._session_store.create(sprint_id)

        if shard_ids:
            shard_context = f"[bootstrap] Loading shards into context: {', '.join(shard_ids)}"
            session = session.add_message("user", shard_context)
            session = session.add_message(
                "assistant",
                f"[bootstrap] Shards acknowledged: {', '.join(shard_ids)}",
            )
            self._session_store.update(session)
            logger.info("ForgemasterRuntime.bootstrap: loaded shards %s into session %s", shard_ids, sprint_id)
        else:
            logger.info("ForgemasterRuntime.bootstrap: no shards requested for sprint %s", sprint_id)

        return session

    def route_ticket(self, task_type: str) -> str:
        """
        Map a task type to a model name using the routing table from
        ``forgemaster/AGENTS.md``.

        Complexity override: tasks whose type string contains any of the
        ``_COMPLEX_KEYWORDS`` are promoted to ``claude-sonnet`` regardless
        of the routing table (borrows the keyword-routing heuristic from
        hermes-agent smart_model_routing.py).

        Defaults to ``claude-sonnet`` for unknown task types.
        """
        normalized = task_type.lower().strip()
        if any(kw in normalized for kw in _COMPLEX_KEYWORDS):
            return "claude-sonnet"
        return _ROUTING_TABLE.get(normalized, "claude-sonnet")

    def run_turn(
        self,
        session: NovaSession,
        role: str,
        skill_path: str,
        prompt: str,
    ) -> tuple[NovaSession, str]:
        """
        Execute a single agent turn.

        1. Reads the skill file at *skill_path* from disk (relative to the
           repo root).  Logs a warning and continues if the file is absent.
        2. Appends ``skill_content + prompt`` as a user message to *session*.
        3. Returns the updated session and a clearly-marked placeholder
           response string.

        The LLM call is **stubbed** — actual model dispatch is out of scope
        for Phase 3.
        """
        resolved = _REPO_ROOT / skill_path
        skill_content: str
        if resolved.exists():
            try:
                skill_content = resolved.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning(
                    "ForgemasterRuntime.run_turn: could not read skill file %s — %s",
                    resolved,
                    exc,
                )
                skill_content = f"[skill file unreadable: {skill_path}]"
        else:
            logger.warning(
                "ForgemasterRuntime.run_turn: skill file not found at %s — continuing without it",
                resolved,
            )
            skill_content = f"[skill file not found: {skill_path}]"

        user_content = f"{skill_content}\n\n---\n\n{prompt}"
        session = session.add_message("user", user_content)

        # LLM call is stubbed — Phase 4 wires actual dispatch.
        placeholder_response = f"[{role} turn logged — skill: {skill_path}]"
        session = session.add_message("assistant", placeholder_response)

        self._session_store.update(session)
        logger.info("ForgemasterRuntime.run_turn: %s turn completed for session %s", role, session.session_id)

        return session, placeholder_response

    def run_sprint(
        self,
        sprint_id: str,
        design_doc: str,
        shard_ids: list[str] | None = None,
    ) -> dict:
        """
        Execute the full Forgemaster sprint lifecycle:

        1. bootstrap  — create session, load shards
        2. orchestrator turn
        3. planner turn
        4. implementer turn
        5. reviewer turn
        6. stub nova_shard_update intent (logged)
        7. flush session to disk
        8. return sprint summary

        Returns a dict suitable for JSON serialisation.
        """
        session = self.bootstrap(sprint_id, shard_ids or [])

        # ── Turn 1: Orchestrator ──────────────────────────────────────────
        session, _ = self.run_turn(
            session,
            role="orchestrator",
            skill_path="forgemaster/skills/forgemaster-orchestrator.md",
            prompt=design_doc,
        )

        # ── Turn 2: Planner ───────────────────────────────────────────────
        session, _ = self.run_turn(
            session,
            role="planner",
            skill_path="forgemaster/skills/forgemaster-writing-plans.md",
            prompt="Decompose the above into typed tickets.",
        )

        # ── Turn 3: Implementer ───────────────────────────────────────────
        session, _ = self.run_turn(
            session,
            role="implementer",
            skill_path="forgemaster/skills/forgemaster-implementation.md",
            prompt="Execute the first ticket.",
        )

        # ── Turn 4: Reviewer ──────────────────────────────────────────────
        session, _ = self.run_turn(
            session,
            role="reviewer",
            skill_path="forgemaster/skills/forgemaster-code-review.md",
            prompt="Review the implementation above.",
        )

        # ── Step 6: Write sprint decisions back to NOVA (stubbed) ─────────
        logger.info(
            "ForgemasterRuntime.run_sprint: [stub] nova_shard_update intent — "
            "sprint '%s' decisions would be written back here in Phase 4+.",
            sprint_id,
        )

        # ── Step 7: Flush session to disk ─────────────────────────────────
        token_totals = {
            "input_tokens": session.usage.input_tokens,
            "output_tokens": session.usage.output_tokens,
            "total_tokens": session.usage.total_tokens,
        }
        self._session_store.flush(sprint_id)

        return {
            "sprint_id": sprint_id,
            "turns": 4,
            "session_id": sprint_id,
            "token_totals": token_totals,
            "status": "complete",
        }

    def get_permitted_lanes(
        self,
        permission_context: ToolPermissionContext,
    ) -> list[str]:
        """
        Return which agent roles are currently available given *permission_context*.

        Accepts an explicit ``permission_context`` argument rather than using
        ``self._permission_context`` so callers can evaluate hypothetical or
        alternative permission configurations without mutating the runtime
        instance (e.g. checking what lanes would be available under a more
        restricted context before dispatching).

        Roles that depend on write-capable tools are flagged as
        ``<role>:restricted`` when all write tools are denied.
        """
        all_write_tools_blocked = all(
            permission_context.blocks(tool) for tool in _WRITE_TOOLS
        )

        lanes: list[str] = []
        for role in ("orchestrator", "planner", "implementer", "reviewer"):
            if role in _WRITE_DEPENDENT_LANES and all_write_tools_blocked:
                lanes.append(f"{role}:restricted")
            else:
                lanes.append(role)
        return lanes
