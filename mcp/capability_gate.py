"""
capability_gate.py — HITL gate keyed to skill verification level.

Maps each NOVA MCP tool to a (capability_tag, is_irreversible) pair and
enforces the gate policy from the NOVA Skill Verification Layer design:

  unverified + irreversible call           → HITL always
  declared/tested + call in @@capabilities → log and proceed (no per-call HITL)
  declared/tested + call outside @@cap     → HITL
  any + call not in @@capabilities         → CapabilityDenied (hard block)

No bypass switch: there is no environment variable, API call, or operator
override that disables the gate, the audit log, or the capability check.

HITL broker modes (NOVA_HITL_BROKER env var):
  interactive (default) — terminal prompt, timeout → deny (dev only)
  policy                — always-deny placeholder until policy file is wired
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from typing import Optional

from skill_manifest import SkillManifest, VerificationLevel

logger = logging.getLogger(__name__)


class CapabilityDenied(Exception):
    """Raised when a tool call is blocked because the capability is not declared."""


class HITLDenied(Exception):
    """Raised when the HITL broker denies an irreversible operation."""


# ── Capability map ────────────────────────────────────────────────────────────
# Maps tool_name → (capability_tag, is_irreversible).
#
# Reversible: shard writes with rollback, in-memory state, reads.
# Irreversible: permanent deletes, external network calls, ODIN state updates,
#               external model spawns.

_CAPABILITY_MAP: dict[str, tuple[str, bool]] = {
    # Read ops — reversible
    "nova_shard_interact":     ("fs.read",        False),
    "nova_shard_search":       ("fs.read",        False),
    "nova_shard_get":          ("fs.read",        False),
    "nova_shard_get_full":     ("fs.read",        False),
    "nova_shard_index":        ("fs.read",        False),
    "nova_shard_summary":      ("fs.read",        False),
    "nova_shard_list":         ("fs.read",        False),
    "nova_graph_query":        ("fs.read",        False),
    "nova_session_list":       ("fs.read",        False),
    "nova_wiki_schema":        ("fs.read",        False),
    "nova_wiki_query":         ("fs.read",        False),
    "nova_wiki_get":           ("fs.read",        False),
    "nova_wiki_list":          ("fs.read",        False),
    "nidhogg_status":          ("fs.read",        False),
    # Reversible writes — transaction buffer
    "nova_shard_create":       ("fs.write.rev",   False),
    "nova_shard_update":       ("fs.write.rev",   False),
    "nova_shard_merge":        ("fs.write.rev",   False),
    "nova_graph_relate":       ("fs.write.rev",   False),
    "nova_session_flush":      ("fs.write.rev",   False),
    "nova_session_load":       ("fs.write.rev",   False),
    "nova_wiki_ingest":        ("fs.write.rev",   False),
    "nova_wiki_lint":          ("fs.write.rev",   False),
    # Irreversible writes
    "nova_shard_archive":      ("fs.write.irrev", True),
    "nova_shard_forget":       ("fs.write.irrev", True),
    "nova_shard_consolidate":  ("fs.write.irrev", True),
    # Memory writes (ODIN state)
    "nova_evolve":             ("memory.write",   True),
    # External / network
    "nidhogg_ingest":          ("net.egress",     True),
    "nidhogg_scan":            ("net.egress",     False),
    # Model invocation
    "gemini_execute_ticket":   ("spawn.proc",     True),
    "gemini_load_file":        ("spawn.proc",     False),
    "nova_forgemaster_sprint": ("spawn.proc",     True),
}

# Fallback for any tool not in the map (externally-registered or future tools).
_DEFAULT_CAPABILITY = ("tool.invoke", False)


def resolve_capability(tool_name: str) -> tuple[str, bool]:
    """Return *(capability_tag, is_irreversible)* for *tool_name*."""
    return _CAPABILITY_MAP.get(tool_name, _DEFAULT_CAPABILITY)


# ── HITL brokers ──────────────────────────────────────────────────────────────

class _InteractiveBroker:
    """
    Blocking terminal prompt.  Times out → deny.

    Only suitable for development / interactive operator sessions.
    The select-based timeout will block the calling thread; callers in async
    contexts should dispatch via asyncio.to_thread (see CapabilityGate.async_check).
    """

    def __init__(self, timeout_s: int = 30) -> None:
        self._timeout_s = timeout_s

    def request(self, tool_name: str, skill_id: str, verification: str) -> bool:
        prompt = (
            f"\n[HITL] Irreversible call intercepted\n"
            f"  Tool:         {tool_name}\n"
            f"  Skill:        {skill_id}\n"
            f"  Verification: {verification}\n"
            f"  Approve? [y/N] (timeout {self._timeout_s}s → deny): "
        )
        try:
            import select
            sys.stdout.write(prompt)
            sys.stdout.flush()
            readable, _, _ = select.select([sys.stdin], [], [], self._timeout_s)
            if readable:
                answer = sys.stdin.readline().strip().lower()
                return answer in ("y", "yes")
            sys.stdout.write("\n[HITL] Timeout — denied.\n")
            sys.stdout.flush()
            return False
        except Exception:
            # Non-interactive context (piped stdin, test runner) — deny.
            return False


class _PolicyBroker:
    """Always-deny placeholder for policy-file mode (v2 concern)."""

    def request(self, tool_name: str, skill_id: str, verification: str) -> bool:
        logger.warning(
            "capability_gate: policy broker has no policy file configured "
            "— denying %s for skill %s",
            tool_name,
            skill_id,
        )
        return False


def _make_broker(mode: str, timeout_s: int) -> _InteractiveBroker | _PolicyBroker:
    if mode == "policy":
        return _PolicyBroker()
    return _InteractiveBroker(timeout_s=timeout_s)


# ── Gate ──────────────────────────────────────────────────────────────────────

class CapabilityGate:
    """
    Middleware gate that enforces capability membership and HITL policy.

    Instantiate once per process with an optional AuditLog.  If no audit_log
    is provided, HITL lifecycle events are not persisted (useful in tests).

    Use check() from synchronous code (forgemaster_runtime).
    Use async_check() from async tool handlers (nova_server).
    """

    def __init__(self, audit_log=None) -> None:
        self._audit = audit_log
        broker_mode = os.environ.get("NOVA_HITL_BROKER", "interactive").lower()
        broker_timeout = int(os.environ.get("NOVA_HITL_TIMEOUT_S", "30"))
        self._broker = _make_broker(broker_mode, broker_timeout)

    # ── Synchronous path (forgemaster_runtime, tests) ─────────────────────

    def check(
        self,
        tool_name: str,
        active_skill: SkillManifest,
        session_id: str,
        target: Optional[str] = None,
    ) -> None:
        """
        Enforce capability and HITL policy for *tool_name*.

        Raises CapabilityDenied  — capability not declared in @@capabilities.
        Raises HITLDenied        — operator rejected the HITL prompt.
        Returns None             — call is allowed to proceed.
        """
        cap_tag, is_irreversible = resolve_capability(tool_name)
        request_id = str(uuid.uuid4())

        # 1. Capability membership check.
        if not active_skill.has_capability(cap_tag):
            if self._audit:
                self._audit.log_denied(
                    session_id=session_id,
                    request_id=request_id,
                    tool_name=tool_name,
                    skill_id=active_skill.skill_id,
                    verification=active_skill.verification.value,
                    reason="undeclared_capability",
                )
            logger.warning(
                "capability_gate: DENIED %s — capability %r not declared in skill %r",
                tool_name,
                cap_tag,
                active_skill.skill_id,
            )
            raise CapabilityDenied(
                f"Tool '{tool_name}' requires capability '{cap_tag}' which is not "
                f"declared in skill '{active_skill.skill_id}'"
            )

        # 2. Reversible calls always proceed without HITL.
        if not is_irreversible:
            return

        # 3. Irreversible — apply HITL policy based on verification level.
        v = active_skill.verification

        if v == VerificationLevel.UNVERIFIED:
            self._run_hitl(tool_name, active_skill, session_id, request_id, target)
            return

        # declared or tested + capability in scope → log and proceed (no per-call HITL).
        if self._audit:
            self._audit.log_request(
                session_id=session_id,
                request_id=request_id,
                tool_name=tool_name,
                skill_id=active_skill.skill_id,
                verification=v.value,
                target=target,
            )
            self._audit.log_executed(
                session_id=session_id,
                request_id=request_id,
                tool_name=tool_name,
                skill_id=active_skill.skill_id,
                verification=v.value,
                target=target,
                ok=True,
            )
        logger.info(
            "capability_gate: %s approved via %s manifest (cap=%s target=%s)",
            tool_name,
            v.value,
            cap_tag,
            target,
        )

    def _run_hitl(
        self,
        tool_name: str,
        active_skill: SkillManifest,
        session_id: str,
        request_id: str,
        target: Optional[str],
    ) -> None:
        """Run the four-state HITL lifecycle and raise HITLDenied if rejected."""
        if self._audit:
            self._audit.log_request(
                session_id=session_id,
                request_id=request_id,
                tool_name=tool_name,
                skill_id=active_skill.skill_id,
                verification=active_skill.verification.value,
                target=target,
            )

        approved = self._broker.request(
            tool_name=tool_name,
            skill_id=active_skill.skill_id,
            verification=active_skill.verification.value,
        )

        if self._audit:
            self._audit.log_decision(
                session_id=session_id,
                request_id=request_id,
                tool_name=tool_name,
                skill_id=active_skill.skill_id,
                verification=active_skill.verification.value,
                approved=approved,
            )

        if not approved:
            raise HITLDenied(
                f"HITL broker denied '{tool_name}' for unverified skill "
                f"'{active_skill.skill_id}'"
            )

        if self._audit:
            self._audit.log_executed(
                session_id=session_id,
                request_id=request_id,
                tool_name=tool_name,
                skill_id=active_skill.skill_id,
                verification=active_skill.verification.value,
                target=target,
                ok=True,
            )

    # ── Async path (nova_server tool handlers) ────────────────────────────

    async def async_check(
        self,
        tool_name: str,
        active_skill: SkillManifest,
        session_id: str,
        target: Optional[str] = None,
    ) -> None:
        """
        Async wrapper for check().

        The blocking HITL broker (select.select terminal prompt) is dispatched
        via asyncio.to_thread so it does not stall the MCP server event loop.
        Non-blocking paths (declared/tested, capability denied) remain synchronous
        inside the thread and return immediately.
        """
        await asyncio.to_thread(self.check, tool_name, active_skill, session_id, target)
