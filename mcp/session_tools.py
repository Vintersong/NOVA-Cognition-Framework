"""
session_tools.py — Forgemaster session persistence tools.

Three handlers (flush / load / list) that wrap ``ServerContext.session_store``.
Registered onto the MCPServer instance via ``register_session_tools(mcp, ctx)``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gate_helpers import permission_reject
from outputs import (
    ErrorPayload,
    SessionFlushed,
    SessionFlushResult,
    SessionList,
    SessionListResult,
    SessionLoaded,
    SessionLoadResult,
    TokenTotals,
)
from reject import RejectCode, reject_model
from schemas import SessionFlushInput, SessionListInput, SessionLoadInput
from tool_registry import nova_tool

if TYPE_CHECKING:
    from server_context import ServerContext


def register_session_tools(mcp, ctx: "ServerContext") -> None:

    @nova_tool(mcp, name="nova_session_flush")
    async def nova_session_flush(params: SessionFlushInput) -> SessionFlushResult:
        """Persist an active session to disk and remove it from memory. Returns JSON confirmation with token totals."""
        if ctx.permission_context.blocks("nova_session_flush"):
            return permission_reject("nova_session_flush")

        session = ctx.session_store.get(params.session_id)
        if session is None:
            return reject_model(
                RejectCode.PRECONDITION_FAILED,
                f"Session '{params.session_id}' is not active in memory.",
                target=params.session_id,
                hint="Use nova_session_load to restore a previously flushed session.",
            )

        try:
            ctx.session_store.flush(params.session_id)
        except Exception as exc:
            return ErrorPayload(message=str(exc))

        return SessionFlushed(
            session_id=params.session_id,
            message_count=len(session.messages),
            token_totals=TokenTotals.from_usage(session.usage),
        )

    @nova_tool(mcp, name="nova_session_load")
    async def nova_session_load(params: SessionLoadInput) -> SessionLoadResult:
        """Load a previously flushed session from disk into memory. Returns JSON with session metadata and message count."""
        if ctx.permission_context.blocks("nova_session_load"):
            return permission_reject("nova_session_load")

        try:
            session = ctx.session_store.load(params.session_id)
        except FileNotFoundError:
            return reject_model(
                RejectCode.PRECONDITION_FAILED,
                f"No persisted session found for '{params.session_id}'.",
                target=params.session_id,
                hint="Call nova_session_list to see which sessions exist.",
                extra={"available": ctx.session_store.list_sessions()},
            )
        except Exception as exc:
            return ErrorPayload(message=str(exc))

        return SessionLoaded(
            session_id=session.session_id,
            message_count=len(session.messages),
            created_at=session.created_at,
            last_active=session.last_active,
            token_totals=TokenTotals.from_usage(session.usage),
        )

    @nova_tool(mcp, name="nova_session_list")
    async def nova_session_list(params: SessionListInput) -> SessionListResult:
        """List all session IDs currently persisted on disk."""
        if ctx.permission_context.blocks("nova_session_list"):
            return permission_reject("nova_session_list")

        sessions = ctx.session_store.list_sessions()
        return SessionList(sessions=sessions, count=len(sessions))
