"""
reject.py — typed reject signal for NOVA's stochastic-deterministic boundary.

Inspired by the SDB contract from Srinivasan, "A Methodology for Selecting and
Composing Runtime Architecture Patterns for Production LLM Agents" (arXiv
2605.20173). The paper identifies the reject signal as a load-bearing part of
the four-part SDB contract (proposer / verifier / commit / reject). NOVA tools
already verify and commit; this module formalises the reject envelope so the
LLM proposer receives a machine-readable signal it can act on instead of
guessing from a free-text "message" field.

The envelope shape:

    {
        "status":     "rejected",
        "code":       "<machine code, see RejectCode>",
        "message":    "<human-readable reason>",
        "retryable":  bool,
        "hint":       "<next action the proposer can take>",
        "target":     "<optional target, e.g. shard_id>",
    }

Tool handlers call ``reject_payload`` and return the resulting JSON string.
Existing ``{"status": "error", "message": ...}`` callers continue to work;
this is the typed successor, not a replacement of the legacy "error" shape
which is reserved for unexpected exceptions.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class RejectCode(str, Enum):
    """Machine-readable reject codes returned to the LLM proposer."""

    SHARD_NOT_FOUND = "shard_not_found"
    WIKI_PAGE_NOT_FOUND = "wiki_page_not_found"
    INVALID_INPUT = "invalid_input"
    PERMISSION_DENIED = "permission_denied"
    GATE_DENIED = "gate_denied"
    QUARANTINED = "quarantined"
    LOW_CONFIDENCE = "low_confidence"
    DUPLICATE = "duplicate"
    DEPENDENCY_MISSING = "dependency_missing"
    PRECONDITION_FAILED = "precondition_failed"
    NOT_IMPLEMENTED = "not_implemented"


# Per-code defaults so call sites can stay terse. Override per call by
# passing explicit ``retryable`` / ``hint`` arguments.
_DEFAULTS: dict[RejectCode, tuple[bool, str]] = {
    RejectCode.SHARD_NOT_FOUND: (
        False,
        "Use nova_shard_search or nova_shard_index to find the right shard_id.",
    ),
    RejectCode.WIKI_PAGE_NOT_FOUND: (
        False,
        "Use nova_wiki_list or nova_wiki_query to discover existing pages.",
    ),
    RejectCode.INVALID_INPUT: (
        True,
        "Re-issue the call with input that satisfies the tool's schema.",
    ),
    RejectCode.PERMISSION_DENIED: (
        False,
        "This tool is blocked in the current permission context.",
    ),
    RejectCode.GATE_DENIED: (
        False,
        "A capability gate refused the operation. Inspect the audit log.",
    ),
    RejectCode.QUARANTINED: (
        True,
        "Target is quarantined until QUARANTINE_HOURS elapses. Retry later or "
        "call nova_shard_query_state to confirm release.",
    ),
    RejectCode.LOW_CONFIDENCE: (
        True,
        "Pass include_low_confidence=True to override the confidence floor.",
    ),
    RejectCode.DUPLICATE: (
        False,
        "A shard with this content already exists; do not re-create.",
    ),
    RejectCode.DEPENDENCY_MISSING: (
        False,
        "Install the required dependency or set the missing env var.",
    ),
    RejectCode.PRECONDITION_FAILED: (
        True,
        "Resolve the precondition and retry. See message for the specific check.",
    ),
    RejectCode.NOT_IMPLEMENTED: (
        False,
        "This branch of the tool is not yet implemented.",
    ),
}


class RejectPayload(BaseModel):
    """The reject envelope, as a model.

    This is the single definition — ``reject_dict`` and ``reject_payload`` are
    both derived from it, and tool handlers name it in their return annotation
    so the published ``outputSchema`` describes the refusal shape alongside the
    success shape.

    ``extra="allow"`` because the envelope is open-ended by design: callers
    merge context in through ``extra`` (``session_tools`` adds ``available``,
    ``nidhogg`` adds ``allowed_roots``).
    """

    model_config = ConfigDict(extra="allow")

    status: Literal["rejected"] = "rejected"
    code: RejectCode = Field(description="Machine-readable reject category.")
    message: str = Field(description="Human-readable explanation for the proposer.")
    retryable: bool = Field(
        description="False means re-issuing the identical call will fail identically.",
    )
    hint: str = Field(default="", description="The next action the proposer can take.")
    target: Optional[str] = Field(
        default=None,
        description="What was rejected — a shard_id, slug, sprint_id or path.",
    )


def reject_model(
    code: RejectCode,
    message: str,
    *,
    retryable: Optional[bool] = None,
    hint: Optional[str] = None,
    target: Optional[str] = None,
    extra: Optional[dict] = None,
) -> RejectPayload:
    """Build the reject envelope. ``retryable`` and ``hint`` fall back to the
    per-code defaults when omitted."""
    default_retryable, default_hint = _DEFAULTS.get(code, (True, ""))
    payload = RejectPayload(
        code=code,
        message=message,
        retryable=default_retryable if retryable is None else retryable,
        hint=default_hint if hint is None else hint,
        target=target,
    )
    if extra:
        for key, value in extra.items():
            # Reserved keys are never overwritten by caller-supplied context.
            if not hasattr(payload, key):
                setattr(payload, key, value)
    return payload


def reject_dict(
    code: RejectCode,
    message: str,
    *,
    retryable: Optional[bool] = None,
    hint: Optional[str] = None,
    target: Optional[str] = None,
    extra: Optional[dict] = None,
) -> dict:
    """Return a typed reject envelope as a dict.

    Use this from helpers that build a payload dict for their caller to
    serialise; :func:`reject_payload` is the JSON-string form tool handlers
    return directly. Arguments are identical.
    """
    payload = reject_model(
        code, message,
        retryable=retryable, hint=hint, target=target, extra=extra,
    ).model_dump(mode="json")
    # `target` is omitted rather than null when there is nothing to name.
    if payload.get("target") is None:
        payload.pop("target", None)
    return payload


def reject_payload(
    code: RejectCode,
    message: str,
    *,
    retryable: Optional[bool] = None,
    hint: Optional[str] = None,
    target: Optional[str] = None,
    extra: Optional[dict] = None,
) -> str:
    """Return a typed reject envelope as a JSON string.

    Args:
        code: Machine-readable reject category from :class:`RejectCode`.
        message: Human-readable explanation for the proposer.
        retryable: If None, falls back to the per-code default.
        hint: If None, falls back to the per-code default. Pass an empty
            string to suppress the hint entirely.
        target: Optional identifier of the rejected target (shard_id, slug,
            sprint_id, etc.). Surfaces in the envelope as the ``target`` key.
        extra: Additional fields to merge into the envelope. Reserved keys
            (status / code / message / retryable / hint / target) are not
            overwritten.
    """
    return json.dumps(
        reject_dict(
            code, message,
            retryable=retryable, hint=hint, target=target, extra=extra,
        ),
        indent=2,
    )


def shard_not_found(shard_id: str) -> str:
    """Shortcut for the most common reject path."""
    return reject_payload(
        RejectCode.SHARD_NOT_FOUND,
        f"Shard '{shard_id}' not found.",
        target=shard_id,
    )
