"""
active_request.py — the MCP request currently being served, for code too deep to
be handed a Context.

SDK v2 removed the ambient ``get_context()``: a handler that wants to talk back
to the client takes ``Context`` as a parameter. That works for handlers, but the
capability gate sits several layers below them — ``gate_check`` →
``CapabilityGate.async_check`` → the HITL broker — and threading a Context down
that chain would mean changing the signature of every gated handler and every
helper in between.

Instead the server middleware records the live request here for its duration,
and the gate reads it when it needs to ask the operator something. The value is
a ``ServerRequestContext``, which carries the two things
``elicit_with_validation`` needs: ``session`` and ``request_id``.

A ContextVar is the right shape: it is per-task, so concurrent tool calls never
see each other's request, and ``asyncio.to_thread`` copies the context into the
worker thread. It is ``None`` outside a request — in tests, and on the
synchronous ``check_capability_tag`` paths — and every reader must handle that.
"""

from __future__ import annotations

import contextlib
from contextvars import ContextVar
from typing import Any, Iterator, Optional

_active: ContextVar[Optional[Any]] = ContextVar("nova_active_mcp_request", default=None)


def current() -> Optional[Any]:
    """The ``ServerRequestContext`` being served, or None outside a request."""
    return _active.get()


@contextlib.contextmanager
def active(request_context: Any) -> Iterator[None]:
    """Record *request_context* as the live request for the enclosing block."""
    token = _active.set(request_context)
    try:
        yield
    finally:
        _active.reset(token)
