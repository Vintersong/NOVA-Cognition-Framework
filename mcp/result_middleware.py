"""
result_middleware.py — mark failed tool calls with ``isError`` on the wire.

Every NOVA handler returns a JSON string, so the SDK wraps it in a successful
``CallToolResult`` and ``isError`` is always false. Deliberate failures — a
missing shard, a permission denial, a capability-gate refusal — were therefore
indistinguishable from success to any client that does not parse NOVA's payload
shape, which is the inverse of what the spec intends: ``isError`` exists exactly
so a client can tell a failed call from a successful one without understanding
the body.

Doing this as server middleware rather than per handler keeps all 41 tools
consistent and means a new tool gets the behaviour for free. The alternative —
having handlers return ``CallToolResult`` directly — would touch every handler
and is better folded into the structured-output work, which rewrites the return
types anyway.

The mapping follows the two-shape envelope contract in ``reject.py``:

    {"status": "rejected", ...}   -> isError = True   (a known refusal)
    {"status": "error", ...}      -> isError = True   (something broke)
    anything else                 -> untouched

``status`` values that denote success — "flushed", "loaded", "added",
"skipped", "updated" and friends — are deliberately not in that set.
"""

from __future__ import annotations

import json
from typing import Any

# Envelope statuses that mean the call did not do what was asked. Kept as a
# frozenset so an unrelated `status` (e.g. "flushed") can never match.
FAILURE_STATUSES: frozenset[str] = frozenset({"rejected", "error"})


def _payload_failed(text: str) -> bool:
    """True if *text* is a NOVA envelope reporting a failure.

    Cheap prefilter first: every envelope is a JSON object mentioning "status",
    so a response that isn't one is rejected without paying for a parse.
    """
    stripped = text.lstrip()
    if not stripped.startswith("{") or '"status"' not in stripped:
        return False
    try:
        payload = json.loads(stripped)
    except (ValueError, TypeError):
        return False
    return isinstance(payload, dict) and payload.get("status") in FAILURE_STATUSES


def result_reports_failure(result: Any) -> bool:
    """True if a ``tools/call`` result dict carries a failure envelope."""
    if not isinstance(result, dict):
        return False
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            if _payload_failed(block.get("text") or ""):
                return True
    return False


async def mark_failed_results(ctx, call_next):
    """Server middleware: set ``isError`` on tool results that report failure.

    Only ``tools/call`` results are inspected, and only to flip ``isError`` from
    false to true — the body is never rewritten, so a client that does parse
    NOVA's envelopes sees exactly what it saw before.
    """
    result = await call_next(ctx)
    if (
        getattr(ctx, "method", None) == "tools/call"
        and isinstance(result, dict)
        and result.get("isError") is False
        and result_reports_failure(result)
    ):
        result["isError"] = True
    return result
