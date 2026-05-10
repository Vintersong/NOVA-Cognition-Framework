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
    Blocking terminal prompt via /dev/tty.  Times out → deny.

    Reads from /dev/tty (the controlling terminal) instead of sys.stdin so it
    does not compete with the MCP JSON-RPC stdio transport.
    Only suitable for development / interactive operator sessions.
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
            tty = open("/dev/tty", "r+")
            tty.write(prompt)
            tty.flush()
            readable, _, _ = select.select([tty], [], [], self._timeout_s)
            if readable:
                answer = tty.readline().strip().lower()
                tty.close()
                return answer in ("y", "yes")
            tty.write("\n[HITL] Timeout — denied.\n")
            tty.flush()
            tty.close()
            return False
        except Exception:
            # No controlling terminal (piped, headless, test runner) — deny.
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
    ) -> Optional[str]:
        """
        Enforce capability and HITL policy for *tool_name*.

        Raises CapabilityDenied  — capability not declared in @@capabilities.
        Raises HITLDenied        — operator rejected the HITL prompt.
        Returns request_id (str) — call is allowed to proceed; the caller MUST
                                   invoke ``audit.log_executed(request_id=…, ok=…)``
                                   after the underlying operation completes so the
                                   audit record reflects the real outcome.
        Returns None             — reversible call; no executed-event needed.
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
            return None

        # 3. Irreversible — apply HITL policy based on verification level.
        v = active_skill.verification

        if v == VerificationLevel.UNVERIFIED:
            self._run_hitl(tool_name, active_skill, session_id, request_id, target)
            return request_id

        # declared or tested + capability in scope → log the request and let
        # the caller mark executed once the underlying operation completes.
        if self._audit:
            self._audit.log_request(
                session_id=session_id,
                request_id=request_id,
                tool_name=tool_name,
                skill_id=active_skill.skill_id,
                verification=v.value,
                target=target,
            )
        logger.info(
            "capability_gate: %s approved via %s manifest (cap=%s target=%s)",
            tool_name,
            v.value,
            cap_tag,
            target,
        )
        return request_id

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
        # NOTE: log_executed is deferred to the caller so the audit record
        # reflects the actual outcome of the operation, not just the gate
        # approval.

    def check_capability_tag(
        self,
        cap_tag: str,
        is_irreversible: bool,
        active_skill: SkillManifest,
        session_id: str,
        virtual_tool_name: str = "fs.write.irrev",
        target: Optional[str] = None,
    ) -> Optional[str]:
        """
        Gate a capability tag directly without routing through a tool name.

        Use this when the action is not a named MCP tool — e.g. file-system
        writes emitted by forgemaster_runtime.  *virtual_tool_name* is used
        only for audit log entries.

        Raises CapabilityDenied or HITLDenied on block.
        Returns request_id (str) — caller MUST call
        ``audit.log_executed(request_id=…, ok=…)`` after the operation
        completes so the audit reflects the real outcome.
        Returns None for reversible calls.
        """
        request_id = str(uuid.uuid4())

        if not active_skill.has_capability(cap_tag):
            if self._audit:
                self._audit.log_denied(
                    session_id=session_id,
                    request_id=request_id,
                    tool_name=virtual_tool_name,
                    skill_id=active_skill.skill_id,
                    verification=active_skill.verification.value,
                    reason="undeclared_capability",
                )
            logger.warning(
                "capability_gate: DENIED %s — capability %r not declared in skill %r",
                virtual_tool_name, cap_tag, active_skill.skill_id,
            )
            raise CapabilityDenied(
                f"Capability '{cap_tag}' not declared in "
                f"skill '{active_skill.skill_id}'"
            )

        if not is_irreversible:
            return None

        v = active_skill.verification
        if v == VerificationLevel.UNVERIFIED:
            self._run_hitl(
                virtual_tool_name, active_skill, session_id, request_id, target
            )
            return request_id

        if self._audit:
            self._audit.log_request(
                session_id=session_id, request_id=request_id,
                tool_name=virtual_tool_name, skill_id=active_skill.skill_id,
                verification=v.value, target=target,
            )
        logger.info(
            "capability_gate: %s approved via %s manifest (cap=%s target=%s)",
            virtual_tool_name, v.value, cap_tag, target,
        )
        return request_id

    # ── Async path (nova_server tool handlers) ────────────────────────────

    async def async_check(
        self,
        tool_name: str,
        active_skill: SkillManifest,
        session_id: str,
        target: Optional[str] = None,
    ) -> Optional[str]:
        """
        Async wrapper for check().

        The blocking HITL broker (select.select terminal prompt) is dispatched
        via asyncio.to_thread so it does not stall the MCP server event loop.
        Non-blocking paths (declared/tested, capability denied) remain synchronous
        inside the thread and return immediately.

        Returns the same request_id as ``check()``; caller is responsible for
        invoking ``audit.log_executed(...)`` post-operation.
        """
        return await asyncio.to_thread(
            self.check, tool_name, active_skill, session_id, target
        )
