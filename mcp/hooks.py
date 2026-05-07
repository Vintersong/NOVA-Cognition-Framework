"""
hooks.py — Lightweight hook event system for NOVA.

Inspired by OpenHarness HookExecutor (Donors/OpenHarness/src/openharness/hooks/).
Replaces bespoke asyncio.create_task() calls scattered across nova_server.py
with a clean event-driven registry pattern.

Events:
    SESSION_START    — fired on nova_shard_interact entry
    PRE_TOOL_USE     — fired before any mutating tool executes (reserved for future use)
    POST_SPRINT      — fired after nova_shard_update completes
    COUNT_THRESHOLD  — fired when shard count reaches NOTT_COUNT_THRESHOLD

Usage in nova_server.py:
    _hooks.emit(NovaHookEvent.SESSION_START)
    _hooks.emit(NovaHookEvent.POST_SPRINT)
    _hooks.emit(NovaHookEvent.COUNT_THRESHOLD)
    await _hooks.emit_wait(NovaHookEvent.POST_SPRINT)  # for explicit invocations

Registration (at startup, after singletons are ready):
    _hooks.register(NovaHookEvent.SESSION_START,
                    lambda: _nott.run(NottTrigger.SESSION_START))
"""

from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# Handler signature: async def handler(**kwargs) -> None
HookHandler = Callable[..., Awaitable[None]]


class NovaHookEvent(Enum):
    SESSION_START    = "session_start"
    PRE_TOOL_USE     = "pre_tool_use"     # reserved — not yet wired
    POST_SPRINT      = "post_sprint"
    COUNT_THRESHOLD  = "count_threshold"


class NovaHookRegistry:
    """
    Minimal event hub.

    Handlers are registered per-event and fired as fire-and-forget
    asyncio tasks so they never block the calling tool.
    Use emit_wait() for the one explicit/user-triggered case
    (nova_shard_consolidate).
    """

    def __init__(self) -> None:
        self._handlers: dict[NovaHookEvent, list[HookHandler]] = {
            e: [] for e in NovaHookEvent
        }

    def register(self, event: NovaHookEvent, handler: HookHandler) -> None:
        """Attach a coroutine handler to an event."""
        self._handlers[event].append(handler)

    def emit(self, event: NovaHookEvent, **kwargs: Any) -> None:
        """
        Fire-and-forget: schedule all handlers as asyncio tasks.
        Never blocks the caller. Safe to call from any async context.
        """
        for handler in self._handlers[event]:
            try:
                asyncio.create_task(handler(**kwargs))
            except RuntimeError:
                # No running event loop (e.g. sync test context) — skip silently.
                logger.debug("NovaHookRegistry.emit: no event loop for %s", event.value)

    async def emit_wait(self, event: NovaHookEvent, **kwargs: Any) -> None:
        """
        Await all handlers in sequence.
        Use for explicit user-requested operations where the result must
        be returned in the same tool call (e.g. nova_shard_consolidate).
        """
        for handler in self._handlers[event]:
            await handler(**kwargs)
