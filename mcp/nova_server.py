"""
nova_server.py — NOVA MCP Server (bootstrap + wiring only).

This module is intentionally thin: it builds the singleton ``ServerContext``,
constructs the ``FastMCP`` instance, and calls each tool category's
``register_*_tools(mcp, ctx)``. Handler bodies live in:

  * ``shard_tools``        — 15 shard CRUD + lifecycle handlers
  * ``graph_tools``        — 2 knowledge-graph handlers
  * ``session_tools``      — 3 Forgemaster session handlers
  * ``forgemaster_tools``  — sprint pipeline + Anthropic cache prewarm
  * ``wiki_tools``         — 6 wiki page handlers
  * ``facts``              — 2 SQLite-backed fact-corpus handlers
  * ``nidhogg``            — 3 repo-scanner handlers
  * ``evolve``             — 1 self-improvement loop
  * ``gemini_mcp``         — 2 Gemini Flash worker handlers
  * ``external_retrieval`` — 1 deliberation-pipeline handler

Singletons (ravens, NÓTT, hook bus, permission context, capability gate,
audit log, session store, usage counters, active skill, server session id)
live on ``ServerContext`` — see ``mcp/server_context.py``. Gate plumbing
(permission_error / gate_check / log_executed) lives in ``mcp/gate_helpers.py``.
"""

from __future__ import annotations

import json
import logging
import os
import sys as _sys
from pathlib import Path

# Load .env from repo root before importing config (config reads env at import time).
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

from mcp.server.fastmcp import FastMCP
from config import (
    CLAUDE_API_KEY as _CLAUDE_API_KEY,
    SHARD_DIR,
    USAGE_LOG_FILE,
)
from graph import load_graph
from nova_embeddings_local import prewarm_embedding_model
from permissions import ToolPermissionContext
from server_context import ServerContext
from store import update_index
from tool_registry import all_names as _registry_all_names

# Tool-category registration entry points.
_sys.path.insert(0, os.path.join(os.path.dirname(__file__), "Gemini"))
from calibrate import register_calibrate_tools
from evolve import register_evolve_tools
from external_retrieval import register_external_retrieval_tools
from facts import register_facts_tools
from forgemaster_tools import register_forgemaster_tools
from gemini_mcp import register_gemini_tools
from graph_tools import register_graph_tools
from nidhogg import register_nidhogg_tools
from session_tools import register_session_tools
from shard_tools import register_shard_tools
from wiki_tools import register_wiki_tools


# ── Bootstrap ────────────────────────────────────────────────────────────────
os.makedirs(SHARD_DIR, exist_ok=True)
prewarm_embedding_model()  # start loading embedding weights in background

_logger = logging.getLogger(__name__)
if not _CLAUDE_API_KEY:
    _logger.warning(
        "CLAUDE_API_KEY not set — running in local-only mode. "
        "Disabled features: HUGINN Haiku re-score, MUNINN Sonnet rerank, "
        "remote summary generation. Local fallbacks (token-overlap, embedding "
        "cosine, structured summaries) still active."
    )

# Process-scoped singletons + default hook wiring. See mcp/server_context.py.
ctx: ServerContext = ServerContext.bootstrap()


# ── FastMCP + tool registration ──────────────────────────────────────────────
mcp = FastMCP("nova_mcp_v2")

# External (non-NOVA-core) tool modules.
register_gemini_tools(mcp, gate=ctx.capability_gate, audit_log=ctx.skill_audit_log)
register_nidhogg_tools(mcp)
register_evolve_tools(mcp)
register_wiki_tools(mcp)
register_facts_tools(mcp)
register_external_retrieval_tools(mcp)
register_calibrate_tools(mcp)

# NOVA-core tool modules (extracted from this file during the refactor).
_shard_handlers = register_shard_tools(mcp, ctx)
register_graph_tools(mcp, ctx)
register_session_tools(mcp, ctx)
register_forgemaster_tools(mcp, ctx)

# Re-export the shard handlers used directly by mcp/test_nova.py.
nova_shard_index = _shard_handlers["nova_shard_index"]
nova_shard_search = _shard_handlers["nova_shard_search"]
nova_shard_get = _shard_handlers["nova_shard_get"]


# ── Public helpers ───────────────────────────────────────────────────────────
# Derived from tool_registry._REGISTRY — the canonical source.
_ALL_TOOL_NAMES: tuple[str, ...] = _registry_all_names()


def get_permitted_tools(permission_context: ToolPermissionContext | None = None) -> list[str]:
    """Return the subset of NOVA tool names not blocked by *permission_context*."""
    pc = permission_context if permission_context is not None else ctx.permission_context
    return [name for name in _ALL_TOOL_NAMES if not pc.blocks(name)]


# ── MCP resources (read-only views) ──────────────────────────────────────────

@mcp.resource("nova://skill")
async def nova_skill() -> str:
    skill_path = Path(__file__).parent / "SKILL.md"
    if skill_path.exists():
        return skill_path.read_text(encoding="utf-8")
    return "SKILL.md not found."


@mcp.resource("nova://index")
async def nova_index() -> str:
    return json.dumps(update_index(), indent=2)


@mcp.resource("nova://graph")
async def nova_graph() -> str:
    return json.dumps(load_graph(), indent=2)


@mcp.resource("nova://usage")
async def nova_usage() -> str:
    """Return last 100 operation log entries plus running session token totals."""
    if not os.path.exists(USAGE_LOG_FILE):
        entries = []
        total_lines = 0
    else:
        lines = []
        try:
            with open(USAGE_LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            pass
        entries = [json.loads(l) for l in lines[-100:] if l.strip()]
        total_lines = len(lines)

    return json.dumps({
        "entries": entries,
        "total": total_lines,
        "session_tokens": {
            "input_tokens": ctx.session_usage.input_tokens,
            "output_tokens": ctx.session_usage.output_tokens,
            "total_tokens": ctx.session_usage.total_tokens,
        },
    }, indent=2)


# ── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    mcp.run()
