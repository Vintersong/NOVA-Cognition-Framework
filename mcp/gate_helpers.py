"""
gate_helpers.py — capability-gate plumbing for NOVA tool handlers.

Every NOVA tool that mutates state (or just wants to be audit-logged) wraps
its body with three helpers:

  1. ``permission_error(tool)`` — returns a typed reject envelope (see
     ``reject.py``) for a tool blocked by the active ``ToolPermissionContext``.
  2. ``gate_check(ctx, tool, target=None)`` — asks the ``CapabilityGate``
     whether the active skill is allowed to run *tool*. Returns
     ``(err_json_or_None, request_id_or_None)``. On allow, the handler
     proceeds and must call ``log_executed`` after the operation.
  3. ``log_executed(ctx, request_id, tool, target, ok)`` — writes the
     terminal ``irreversible.executed`` audit-log event.

These functions all take an explicit ``ServerContext`` so the extracted
tool modules (shard_tools, graph_tools, session_tools, forgemaster_tools)
can call them without reaching into module globals.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from capability_gate import CapabilityDenied, HITLDenied
from permissions import denial_payload
from reject import RejectCode, RejectPayload, reject_model, reject_payload

if TYPE_CHECKING:
    from server_context import ServerContext

_logger = logging.getLogger(__name__)


def permission_error(tool_name: str) -> str:
    """Return the typed reject envelope for a blocked tool call, as JSON.

    Delegates to ``permissions.denial_payload`` so the permission-denied shape
    is defined once. Used by handlers that still return ``str``; handlers with
    a typed return annotation use :func:`permission_reject` instead.
    """
    return denial_payload(tool_name)


def permission_reject(tool_name: str) -> RejectPayload:
    """The same envelope as :func:`permission_error`, as a model.

    Handlers annotated with an output model return this so the refusal
    validates against the published ``outputSchema``.
    """
    return reject_model(
        RejectCode.PERMISSION_DENIED,
        f"Tool '{tool_name}' is not permitted in the current permission context.",
        target=tool_name,
    )


async def gate_check_model(
    ctx: "ServerContext",
    tool_name: str,
    target: str | None = None,
    approval: bool | None = None,
) -> tuple[RejectPayload | None, str | None]:
    """Run the capability gate for *tool_name* against the active skill.

    *approval* carries the operator's answer for a destructive tool, obtained by
    the handler's ``Approval(...)`` parameter before the body ran. Pass
    ``was_approved(approval_param)``; the gate records the decision and refuses
    when it is False.

    Returns ``(refusal, request_id)``:
      - On allow: ``(None, request_id_or_None)``. The handler must call
        :func:`log_executed` after the operation completes so the audit
        record reflects the real outcome.
      - On block: ``(RejectPayload, None)`` — the handler returns it directly.

    :func:`gate_check` is the JSON-string form, for handlers that have not yet
    been given a typed return annotation.
    """
    try:
        request_id = await ctx.capability_gate.async_check(
            tool_name, ctx.active_skill, ctx.server_session_id, target,
            approval=approval,
        )
        return None, request_id
    except CapabilityDenied as exc:
        # The skill's @@capabilities do not cover this tool. Re-running as-is
        # cannot help — the manifest has to change.
        return reject_model(
            RejectCode.GATE_DENIED, str(exc), target=tool_name, retryable=False,
        ), None
    except HITLDenied as exc:
        # A human declined (or the prompt timed out). A later call may be
        # approved, so this one is retryable.
        return reject_model(
            RejectCode.GATE_DENIED, str(exc), target=tool_name, retryable=True,
            hint="A human declined or did not answer in time. Ask the operator "
                 "to approve, then retry.",
        ), None


async def gate_check(
    ctx: "ServerContext",
    tool_name: str,
    target: str | None = None,
    approval: bool | None = None,
) -> tuple[str | None, str | None]:
    """JSON-string form of :func:`gate_check_model`, for untyped handlers."""
    refusal, request_id = await gate_check_model(ctx, tool_name, target, approval)
    return (None if refusal is None else refusal.model_dump_json(indent=2)), request_id


def log_executed(
    ctx: "ServerContext",
    request_id: str | None,
    tool_name: str,
    target: str | None,
    ok: bool,
) -> None:
    """Record an irreversible.executed event for a previously-gated call."""
    if request_id is None:
        return
    try:
        ctx.skill_audit_log.log_executed(
            session_id=ctx.server_session_id,
            request_id=request_id,
            tool_name=tool_name,
            skill_id=ctx.active_skill.skill_id,
            verification=ctx.active_skill.verification.value,
            target=target,
            ok=ok,
        )
    except Exception as exc:  # pragma: no cover — audit log must never crash handlers
        _logger.warning("gate_helpers.log_executed failed for %s: %s", tool_name, exc)
