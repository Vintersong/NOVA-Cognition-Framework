"""
session_tools.py — Forgemaster session persistence tools.

Three handlers (flush / load / list) that wrap ``ServerContext.session_store``.
Registered onto the FastMCP instance via ``register_session_tools(mcp, ctx)``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from gate_helpers import permission_error
from schemas import SessionFlushInput, SessionListInput, SessionLoadInput
from tool_registry import nova_tool

if TYPE_CHECKING:
    from server_context import ServerContext


def register_session_tools(mcp, ctx: "ServerContext") -> None:

    @nova_tool(mcp, name="nova_session_flush")
    async def nova_session_flush(params: SessionFlushInput) -> str:
        """Persist an active session to disk and remove it from memory. Returns JSON confirmation with token totals."""
        if ctx.permission_context.blocks("nova_session_flush"):
            return permission_error("nova_session_flush")

        session = ctx.session_store.get(params.session_id)
        if session is None:
            return json.dumps({
                "error": f"Session '{params.session_id}' is not active in memory.",
                "hint": "Use nova_session_load to restore a previously flushed session.",
            }, indent=2)

        try:
            ctx.session_store.flush(params.session_id)
        except Exception as exc:
            return json.dumps({"error": str(exc)}, indent=2)

        return json.dumps({
            "status": "flushed",
            "session_id": params.session_id,
            "message_count": len(session.messages),
            "token_totals": {
                "input_tokens": session.usage.input_tokens,
                "output_tokens": session.usage.output_tokens,
                "total_tokens": session.usage.total_tokens,
            },
        }, indent=2)

    @nova_tool(mcp, name="nova_session_load")
    async def nova_session_load(params: SessionLoadInput) -> str:
        """Load a previously flushed session from disk into memory. Returns JSON with session metadata and message count."""
        if ctx.permission_context.blocks("nova_session_load"):
            return permission_error("nova_session_load")

        try:
            session = ctx.session_store.load(params.session_id)
        except FileNotFoundError:
            return json.dumps({
                "error": f"No persisted session found for '{params.session_id}'.",
                "available": ctx.session_store.list_sessions(),
            }, indent=2)
        except Exception as exc:
            return json.dumps({"error": str(exc)}, indent=2)

        return json.dumps({
            "status": "loaded",
            "session_id": session.session_id,
            "message_count": len(session.messages),
            "created_at": session.created_at,
            "last_active": session.last_active,
            "token_totals": {
                "input_tokens": session.usage.input_tokens,
                "output_tokens": session.usage.output_tokens,
                "total_tokens": session.usage.total_tokens,
            },
        }, indent=2)

    @nova_tool(mcp, name="nova_session_list")
    async def nova_session_list(params: SessionListInput) -> str:
        """List all session IDs currently persisted on disk."""
        if ctx.permission_context.blocks("nova_session_list"):
            return permission_error("nova_session_list")

        sessions = ctx.session_store.list_sessions()
        return json.dumps({
            "status": "ok",
            "sessions": sessions,
            "count": len(sessions),
        }, indent=2)
