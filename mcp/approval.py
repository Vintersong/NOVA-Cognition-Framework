"""
approval.py — ask the operator before a destructive tool runs.

The capability gate decides *whether* a call needs human approval
(``tool_registry.requires_approval``, driven by the same capability classes that
publish ``destructiveHint``). This module does the *asking*, because on the
current protocol the asking cannot happen where the gate sits.

Why not from inside the gate: MCP 2026-07-28 forbids server-initiated requests,
so a handler cannot pause mid-body and call ``elicitation/create`` — the
connection has no back-channel. The sanctioned mechanism is a resolver: a
parameter annotated ``Annotated[T, Resolve(fn)]`` is filled before the body runs,
and the SDK routes the question over whatever the negotiated protocol supports —
batched into an ``InputRequiredResult`` on 2026-07-28, a standalone request on
2025-11-25 and earlier.

So a destructive handler declares an approval parameter, and hands the answer to
``gate_check``. The gate still owns the policy and the whole audit trail; only
the question travels through the resolver.

Resolver parameters are injected, not LLM-supplied, so they do not appear in the
tool's published input schema.
"""

from __future__ import annotations

from typing import Annotated, Any, Optional, Union

from mcp.server.mcpserver import (
    AcceptedElicitation,
    Context,
    Elicit,
    ElicitationResult,
    Resolve,
)
from pydantic import BaseModel, Field

from tool_registry import get as _registry_get
from tool_registry import requires_approval

# Fields a target might live under, most specific first. Used only to make the
# prompt concrete ("Forget shard nova_x?" beats "Forget a shard?").
_TARGET_FIELDS = ("shard_id", "file_path", "sprint_id", "slug", "source_id")


class ApprovalDecision(BaseModel):
    """The operator's answer. Primitive-only, as elicitation schemas require."""

    approve: bool = Field(
        description="Allow this destructive operation to run? It cannot be undone.",
    )


def _target_of(params: Any) -> Optional[str]:
    for field in _TARGET_FIELDS:
        value = getattr(params, field, None)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, (list, tuple)) and value:
            return ", ".join(str(v) for v in value)
    return None


def _prompt(tool_name: str, params: Any) -> str:
    spec = _registry_get(tool_name)
    target = _target_of(params)
    lines = [
        f"NOVA wants to run {spec.title or tool_name} ({tool_name}).",
        "This cannot be undone.",
    ]
    if target:
        lines.insert(1, f"Target: {target}")
    return "\n".join(lines)


def approval_for(tool_name: str):
    """Build the resolver for *tool_name*'s approval parameter.

    Returns a decision without asking when the tool does not need approval, or
    when the client cannot be asked — a plain return is treated as an accepted
    outcome carrying that value, so the handler always sees a decision and can
    turn a refusal into a typed reject rather than a protocol error.
    """

    async def resolve_approval(
        ctx: Context, params: Any = None
    ) -> Union[Elicit[ApprovalDecision], ApprovalDecision]:
        if not requires_approval(tool_name):
            return ApprovalDecision(approve=True)

        capabilities = ctx.client_capabilities
        if capabilities is None or getattr(capabilities, "elicitation", None) is None:
            # Nothing to ask. Deny rather than proceed, and rather than raising
            # the SDK's missing-capability error at the caller.
            return ApprovalDecision(approve=False)

        return Elicit(_prompt(tool_name, params), ApprovalDecision)

    resolve_approval.__name__ = f"resolve_approval_{tool_name}"
    return resolve_approval


def Approval(tool_name: str):
    """The type annotation for a destructive handler's approval parameter.

    Usage::

        async def nova_shard_forget(
            params: ShardForgetInput,
            approval: Approval("nova_shard_forget"),
        ) -> str:
    """
    return Annotated[
        ElicitationResult[ApprovalDecision], Resolve(approval_for(tool_name))
    ]


def was_approved(outcome: Any) -> bool:
    """True only if the operator affirmatively approved.

    Declines, cancellations and a client that could not be asked all read as a
    refusal.
    """
    return (
        isinstance(outcome, AcceptedElicitation)
        and isinstance(getattr(outcome, "data", None), ApprovalDecision)
        and outcome.data.approve
    )
