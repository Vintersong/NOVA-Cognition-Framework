"""
capability_gate.py — HITL gate keyed to skill verification level.

Maps each NOVA MCP tool to a (capability_tag, is_irreversible) pair and
enforces the gate policy from the NOVA Skill Verification Layer design:

  call not in @@capabilities               → CapabilityDenied (hard block)
  reversible call                          → proceed (no audit record)
  destructive capability, any verification → HITL always
  unverified + irreversible                → HITL always
  declared/tested + irreversible           → log the request and proceed

"Destructive" is tool_registry.DESTRUCTIVE_CAPABILITIES — the same set that
drives the destructiveHint published to clients, so the hint a caller sees and
the prompt the operator gets can never diverge. Note check_capability_tag()
(the un-named filesystem-write path) keeps the unverified-only rule: the tool
that spawned it is already gated at entry, and gating each write as well would
stall every sprint.

No bypass switch: there is no environment variable, API call, or operator
override that disables the gate, the audit log, or the capability check.

How the operator is asked:

  MCP tool calls   — through elicitation, by the resolver in approval.py, which
                     runs before the handler body. MCP 2026-07-28 forbids
                     server-initiated requests, so the gate cannot ask from
                     where it sits; the handler passes the answer in as
                     ``approval=``.
  everything else  — through a broker below. This covers check_capability_tag()
                     (forgemaster's per-file writes, the Gemini worker) and bare
                     CLI use, where there is no MCP client to ask.

HITL broker modes (NOVA_HITL_BROKER env var):
  interactive (default) — terminal prompt, timeout → deny. Needs a controlling
                          terminal, so it denies under any MCP client.
  policy                — always-deny placeholder until policy file is wired
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import uuid
from typing import Optional

from skill_manifest import SkillManifest, VerificationLevel
from tool_registry import DESTRUCTIVE_CAPABILITIES
from tool_registry import capability_map as _registry_capability_map

logger = logging.getLogger(__name__)


class CapabilityDenied(Exception):
    """Raised when a tool call is blocked because the capability is not declared."""


class HITLDenied(Exception):
    """Raised when the HITL broker denies an irreversible operation."""


# ── Capability map ────────────────────────────────────────────────────────────
# Derived from `mcp/tool_registry.py:_REGISTRY` — the canonical source. The
# resolve_capability path hard-fails on unknown tools rather than granting a
# default capability, so a forgotten registry entry can never silently bypass
# the gate.

_CAPABILITY_MAP: dict[str, tuple[str, bool]] = _registry_capability_map()


def resolve_capability(tool_name: str) -> tuple[str, bool]:
    """Return *(capability_tag, is_irreversible)* for *tool_name*.

    Raises ``CapabilityDenied`` if *tool_name* is not declared in
    ``mcp/tool_registry.py``. There is no default-capability fallback.
    """
    try:
        return _CAPABILITY_MAP[tool_name]
    except KeyError:
        raise CapabilityDenied(
            f"Tool '{tool_name}' is not declared in mcp/tool_registry.py. "
            f"Refusing to grant a default capability."
        )


# ── HITL brokers ──────────────────────────────────────────────────────────────

class _InteractiveBroker:
    """
    Blocking terminal prompt with timeout → deny.

    On Unix reads from /dev/tty; on Windows uses CONOUT$/CONIN$ with a
    daemon thread so select (unavailable on Windows file handles) is not needed.
    Reads from the controlling terminal rather than sys.stdin so the prompt
    does not compete with the MCP JSON-RPC stdio transport.
    Only suitable for development / interactive operator sessions.
    """

    def __init__(self, timeout_s: int = 30) -> None:
        self._timeout_s = timeout_s

    def request(
        self, tool_name: str, skill_id: str, verification: str,
        target: Optional[str] = None,
    ) -> bool:
        prompt = (
            f"\n[HITL] Destructive call intercepted\n"
            f"  Tool:         {tool_name}\n"
            f"  Target:       {target or '—'}\n"
            f"  Skill:        {skill_id}\n"
            f"  Verification: {verification}\n"
            f"  Approve? [y/N] (timeout {self._timeout_s}s → deny): "
        )
        try:
            if os.name == "nt":
                return self._request_windows(prompt)
            return self._request_unix(prompt)
        except Exception:
            # No controlling terminal (piped, headless, test runner) — deny.
            return False

    def _request_unix(self, prompt: str) -> bool:
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

    def _request_windows(self, prompt: str) -> bool:
        # msvcrt polling keeps everything in the calling thread — no daemon
        # thread is left blocked after a timeout, so no stale reader can
        # consume a future operator approval.
        import sys
        import msvcrt  # Windows-only stdlib; conditional branch (os.name == "nt")
        import time

        # Headless guard: when stdin is a pipe (MCP stdio transport) msvcrt.kbhit()
        # polls the pipe handle and getwche() would consume JSON-RPC bytes, corrupting
        # the server's message stream. Deny immediately in non-interactive processes.
        if not sys.stdin.isatty():
            return False

        try:
            with open("CONOUT$", "w") as cout:
                cout.write(prompt)
                cout.flush()
        except Exception:
            return False

        deadline = time.monotonic() + self._timeout_s
        chars: list[str] = []

        while time.monotonic() < deadline:
            if msvcrt.kbhit():  # type: ignore[attr-defined]
                ch = msvcrt.getwche()  # type: ignore[attr-defined]  # echoes the character
                if ch in ("\r", "\n"):
                    break
                chars.append(ch)
            else:
                time.sleep(0.05)
        else:
            # Timeout — drain buffered keystrokes so they don't bleed into
            # the next prompt, then deny.
            while msvcrt.kbhit():  # type: ignore[attr-defined]
                msvcrt.getwch()  # type: ignore[attr-defined]
            try:
                with open("CONOUT$", "w") as cout:
                    cout.write("\n[HITL] Timeout — denied.\n")
                    cout.flush()
            except Exception:
                pass
            return False

        return "".join(chars).strip().lower() in ("y", "yes")


class _PolicyBroker:
    """Always-deny placeholder for policy-file mode (v2 concern)."""

    def request(
        self, tool_name: str, skill_id: str, verification: str,
        target: Optional[str] = None,
    ) -> bool:
        logger.warning(
            "capability_gate: policy broker has no policy file configured "
            "— denying %s for skill %s",
            tool_name,
            skill_id,
        )
        return False


def _make_broker(mode: str, timeout_s: int) -> _InteractiveBroker | _PolicyBroker:
    """Fallback broker for callers that cannot ask the MCP client.

    On the MCP tool path the operator is asked through elicitation, by the
    resolver in ``approval.py``, and the answer is handed to the gate — these
    brokers are never consulted there. They remain for the synchronous
    ``check_capability_tag`` callers (forgemaster's per-file writes, the Gemini
    worker) and for bare-CLI use, where there is no client to ask.
    """
    if mode == "policy":
        return _PolicyBroker()
    if mode not in ("interactive", "auto"):
        logger.warning(
            "capability_gate: unknown NOVA_HITL_BROKER=%r — using 'interactive'", mode,
        )
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
        request_id, action, cap_tag = self._authorize(
            tool_name, active_skill, session_id
        )
        if action == "proceed":
            return None
        if action == "hitl":
            self._run_hitl(tool_name, active_skill, session_id, request_id, target)
            return request_id
        self._log_manifest_approval(
            tool_name, active_skill, session_id, request_id, cap_tag, target
        )
        return request_id

    # ── Shared policy ────────────────────────────────────────────────────

    def _authorize(
        self,
        tool_name: str,
        active_skill: SkillManifest,
        session_id: str,
    ) -> tuple[str, str, str]:
        """Decide what happens to a call, without performing it.

        Returns ``(request_id, action, cap_tag)`` where *action* is one of
        ``"proceed"`` (reversible — nothing to audit), ``"hitl"`` (a human must
        approve) or ``"manifest"`` (the skill's verification level vouches for
        it). Raises ``CapabilityDenied`` for an undeclared capability.

        Both ``check`` and ``async_check`` route through here so the policy is
        stated once; only the way HITL is carried out differs between them.
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
            return request_id, "proceed", cap_tag

        # 3. A destructive tool is confirmed by a human whatever the skill
        # claims. Without this, nothing on the MCP tool path could ever reach
        # the broker: ServerContext bootstraps active_skill to OPERATOR_DIRECT,
        # which is TESTED with a wildcard capability.
        #
        # Keyed on the capability class, not the `irreversible` flag —
        # nova_graph_relate sets that flag purely to mint an audit request_id and
        # must not prompt on ordinary corroboration. The same set drives the
        # destructiveHint clients see, so the two can never disagree.
        if (
            active_skill.verification == VerificationLevel.UNVERIFIED
            or cap_tag in DESTRUCTIVE_CAPABILITIES
        ):
            return request_id, "hitl", cap_tag

        return request_id, "manifest", cap_tag

    def _log_manifest_approval(
        self,
        tool_name: str,
        active_skill: SkillManifest,
        session_id: str,
        request_id: str,
        cap_tag: str,
        target: Optional[str],
    ) -> None:
        """Record an irreversible call vouched for by the skill's manifest."""
        v = active_skill.verification
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

    def _hitl_begin(
        self,
        tool_name: str,
        active_skill: SkillManifest,
        session_id: str,
        request_id: str,
        target: Optional[str],
    ) -> None:
        """Record the approval request, before the operator is asked."""
        if self._audit:
            self._audit.log_request(
                session_id=session_id,
                request_id=request_id,
                tool_name=tool_name,
                skill_id=active_skill.skill_id,
                verification=active_skill.verification.value,
                target=target,
            )

    def _hitl_record(
        self,
        tool_name: str,
        active_skill: SkillManifest,
        session_id: str,
        request_id: str,
        approved: bool,
    ) -> None:
        """Record the decision, for approvals and refusals alike.

        Always written, and always before any raise:
        ``audit_log.run_biconditional_check`` reads a dangling
        ``irreversible.request`` with no decision as a gate bypass.
        """
        if self._audit:
            self._audit.log_decision(
                session_id=session_id,
                request_id=request_id,
                tool_name=tool_name,
                skill_id=active_skill.skill_id,
                verification=active_skill.verification.value,
                approved=approved,
            )

    def _hitl_finish(
        self,
        tool_name: str,
        active_skill: SkillManifest,
        session_id: str,
        request_id: str,
        approved: bool,
    ) -> None:
        """Record the decision, then refuse the call if it was not approved."""
        self._hitl_record(
            tool_name, active_skill, session_id, request_id, approved
        )
        if not approved:
            raise HITLDenied(
                f"HITL broker denied '{tool_name}' for skill "
                f"'{active_skill.skill_id}'"
            )
        # NOTE: log_executed is deferred to the caller so the audit record
        # reflects the actual outcome of the operation, not just the gate
        # approval.

    def _run_hitl(
        self,
        tool_name: str,
        active_skill: SkillManifest,
        session_id: str,
        request_id: str,
        target: Optional[str],
    ) -> None:
        """Synchronous HITL lifecycle. Raises HITLDenied if refused."""
        self._hitl_begin(tool_name, active_skill, session_id, request_id, target)
        try:
            approved = self._broker.request(
                tool_name=tool_name,
                skill_id=active_skill.skill_id,
                verification=active_skill.verification.value,
                target=target,
            )
        except BaseException:
            # A broker that blew up is a refusal, and the decision still has to
            # be recorded or the audit shows a request with no outcome. Record
            # it, then let the original error surface rather than masking it
            # with HITLDenied — the cause is worth seeing.
            self._hitl_record(
                tool_name, active_skill, session_id, request_id, approved=False
            )
            raise
        self._hitl_finish(
            tool_name, active_skill, session_id, request_id, approved
        )

    async def _run_hitl_async(
        self,
        tool_name: str,
        active_skill: SkillManifest,
        session_id: str,
        request_id: str,
        target: Optional[str],
        approval: Optional[bool] = None,
    ) -> None:
        """HITL lifecycle on the event loop.

        When *approval* is supplied the operator has already answered (via
        elicitation, before the handler body) and no broker runs. Otherwise the
        fallback broker is consulted — in a worker thread, since it blocks.
        """
        self._hitl_begin(tool_name, active_skill, session_id, request_id, target)
        if approval is not None:
            self._hitl_finish(
                tool_name, active_skill, session_id, request_id, approval
            )
            return
        try:
            # The fallback brokers all block (a terminal read), so they run off
            # the event loop.
            approved = await asyncio.to_thread(
                functools.partial(
                    self._broker.request,
                    tool_name=tool_name,
                    skill_id=active_skill.skill_id,
                    verification=active_skill.verification.value,
                    target=target,
                )
            )
        except BaseException:
            # Includes CancelledError when the client disconnects mid-prompt.
            self._hitl_record(
                tool_name, active_skill, session_id, request_id, approved=False
            )
            raise
        self._hitl_finish(
            tool_name, active_skill, session_id, request_id, approved
        )

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
        approval: Optional[bool] = None,
    ) -> Optional[str]:
        """
        Async counterpart to check(), used by the MCP tool handlers.

        Applies the same policy via ``_authorize``, but carries out HITL on the
        event loop so the elicitation broker can await the client. A broker with
        no async path (the terminal prompt) is dispatched to a worker thread so
        it still does not stall the server.

        *approval* is the operator's answer when the caller already asked — the
        MCP path elicits through ``approval.py`` before the handler body runs,
        because the protocol forbids asking from inside it. When supplied it is
        used directly and no broker is consulted; the audit trail is written
        either way.

        Returns the same request_id as ``check()``; the caller is responsible for
        invoking ``audit.log_executed(...)`` post-operation.
        """
        request_id, action, cap_tag = self._authorize(
            tool_name, active_skill, session_id
        )
        if action == "proceed":
            return None
        if action == "hitl":
            await self._run_hitl_async(
                tool_name, active_skill, session_id, request_id, target, approval
            )
            return request_id
        self._log_manifest_approval(
            tool_name, active_skill, session_id, request_id, cap_tag, target
        )
        return request_id
