"""
nova_server.py — NOVA MCP Server (bootstrap + wiring only).

This module is intentionally thin: it builds the singleton ``ServerContext``,
constructs the ``MCPServer`` instance, and calls each tool category's
``register_*_tools(mcp, ctx)``. Handler bodies live in:

  * ``shard_tools``        — 16 shard CRUD + lifecycle handlers
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

from mcp.server.mcpserver import MCPServer
from mcp.types import Completion, PromptReference, ResourceTemplateReference
from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError

from config import (
    CLAUDE_API_KEY as _CLAUDE_API_KEY,
    SHARD_DIR,
    USAGE_LOG_FILE,
)
from graph import load_graph
from nova_embeddings_local import prewarm_embedding_model
from permissions import ToolPermissionContext
from result_middleware import mark_failed_results
from server_context import ServerContext
from shard_format import load_shard_file
from store import update_index
from wiki import all_wiki_pages, load_wiki_page, load_wiki_schema
from tool_registry import all_names as _registry_all_names

# Tool-category registration entry points.
_sys.path.insert(0, os.path.join(os.path.dirname(__file__), "Gemini"))
from calibrate import register_calibrate_tools
from code_index import register_code_index_tools
from evolve import register_evolve_tools
from huginn_tools import register_huginn_tools
from external_retrieval import register_external_retrieval_tools
from facts import register_facts_tools
from forgemaster_tools import register_forgemaster_tools
from gemini_mcp import register_gemini_tools
from graph_tools import register_graph_tools
from nidhogg import register_nidhogg_tools
from prompts import register_prompts
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


# ── Server + tool registration ───────────────────────────────────────────────

try:  # present when the project is pip-installed; falls back for a bare checkout
    from importlib.metadata import version as _pkg_version

    SERVER_VERSION = _pkg_version("nova-cognition-framework")
except Exception:  # pragma: no cover — packaging metadata absent
    SERVER_VERSION = "0.1.0"

# Surfaced to the client on every request. NOVA is a memory server: loading
# context before acting is the whole contract, so say so here rather than
# relying on CLAUDE.md, which only this repo's own agent ever reads.
SERVER_INSTRUCTIONS = """\
NOVA is a persistent memory server. Shards are conversation-scoped memory units
carrying a confidence score that decays over time.

Call nova_shard_interact first to load relevant context — most other tools are
far less useful without it. Read nova://skill for the full operating protocol.

Tools are annotated: readOnlyHint marks safe reads, destructiveHint marks
operations that cannot be undone (nova_shard_forget, nova_shard_archive,
nidhogg_ingest/scan, nova_evolve, nova_forgemaster_sprint).

Errors arrive as JSON carrying a "status" field: "rejected" means the tool
refused for a known reason and the payload has a machine-readable "code",
"retryable" flag and "hint"; "error" means something unexpected broke.
"""

mcp = MCPServer(
    "nova_mcp_v2",
    title="NOVA Cognition Framework",
    version=SERVER_VERSION,
    instructions=SERVER_INSTRUCTIONS,
    # A handler returns a payload, not a CallToolResult, so the SDK would
    # report every call as a success. This flips isError on failure envelopes.
    middleware=[mark_failed_results],
)

# External (non-NOVA-core) tool modules.
register_gemini_tools(mcp, gate=ctx.capability_gate, audit_log=ctx.skill_audit_log)
register_nidhogg_tools(mcp, ctx)
register_evolve_tools(mcp, ctx)
register_huginn_tools(mcp, ctx)
register_wiki_tools(mcp)
register_facts_tools(mcp)
register_external_retrieval_tools(mcp)
register_calibrate_tools(mcp)
register_code_index_tools(mcp, ctx)

# NOVA-core tool modules (extracted from this file during the refactor).
_shard_handlers = register_shard_tools(mcp, ctx)
register_graph_tools(mcp, ctx)
register_session_tools(mcp, ctx)
register_forgemaster_tools(mcp, ctx)

# Prompts: six workflow openers plus one per core forgemaster skill.
register_prompts(mcp)

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
#
# Resources are not tools, so nothing routes them through the permission
# context the way every @nova_tool handler does. That is a hole wherever a
# resource serves the same data as a gateable tool: denying nova_shard_get
# would have meant nothing while nova://shard/{id} still handed out bodies. The
# helper below closes it by naming, per resource, the tool whose denial covers
# it. nova://skill and nova://usage have no tool equivalent — the first is
# NOVA's own instructions and the second is a local operation log — so they
# stay open.

def _require(tool_name: str) -> None:
    """Refuse a resource read that a denied tool would also have refused."""
    if ctx.permission_context.blocks(tool_name):
        raise ResourceError(
            f"Blocked: this resource serves the same data as '{tool_name}', "
            "which is not permitted in the current permission context."
        )


@mcp.resource(
    "nova://skill",
    name="nova_skill",
    title="NOVA Skill Definition",
    description=(
        "SKILL.md — NOVA's operating instructions: the shard model, the tool "
        "inventory, and the session protocol. Read this first."
    ),
    mime_type="text/markdown",
)
async def nova_skill() -> str:
    skill_path = Path(__file__).parent / "SKILL.md"
    if not skill_path.exists():
        # Returning the words "SKILL.md not found." would have been served to
        # the client as though it were the skill.
        raise ResourceNotFoundError("SKILL.md is missing from the install.")
    return skill_path.read_text(encoding="utf-8")


@mcp.resource(
    "nova://index",
    name="nova_index",
    title="Shard Index",
    description=(
        "The full shard index: one metadata row per shard (id, guiding "
        "question, confidence, tags, timestamps). Rebuilt on read."
    ),
    mime_type="application/json",
)
async def nova_index() -> str:
    _require("nova_shard_index")
    return json.dumps(update_index(), indent=2)


@mcp.resource(
    "nova://graph",
    name="nova_graph",
    title="Shard Knowledge Graph",
    description="Every directed inter-shard relation as stored on disk.",
    mime_type="application/json",
)
async def nova_graph() -> str:
    _require("nova_graph_query")
    return json.dumps(load_graph(), indent=2)


@mcp.resource(
    "nova://usage",
    name="nova_usage",
    title="Operation Log and Token Usage",
    description=(
        "The last 100 operation-log entries plus running session token totals."
    ),
    mime_type="application/json",
)
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


# ── MCP resource templates (parameterised reads) ─────────────────────────────
#
# The corpus is the point of a memory server, and until now a client could only
# reach it by calling a tool. These give shards and wiki pages a URI, so they
# can be linked, embedded in a prompt, and completed against (see the
# completion handlers below).

@mcp.resource(
    "nova://shard/{shard_id}",
    name="nova_shard",
    title="Shard",
    description=(
        "One shard by id, exactly as stored on disk — metadata, conversation "
        "history and context. The read-only equivalent of nova_shard_get."
    ),
    mime_type="application/json",
)
async def nova_shard_resource(shard_id: str) -> str:
    _require("nova_shard_get")
    try:
        data, _ = load_shard_file(shard_id, SHARD_DIR)
    except FileNotFoundError:
        raise ResourceNotFoundError(f"Shard '{shard_id}' not found.") from None
    except ValueError as exc:
        # load_shard_file raises this when the id escapes the shard directory.
        raise ResourceError(str(exc)) from None
    return json.dumps(data, indent=2)


@mcp.resource(
    "nova://wiki/{slug}",
    name="nova_wiki_page",
    title="Wiki Page",
    description=(
        "One curated wiki page by slug, as Markdown. The read-only equivalent "
        "of nova_wiki_get."
    ),
    mime_type="text/markdown",
)
async def nova_wiki_resource(slug: str) -> str:
    _require("nova_wiki_get")
    page = load_wiki_page(slug)
    if page is None:
        raise ResourceNotFoundError(f"No wiki page found for slug '{slug}'.")
    return page.full_text


# ── Argument completion ──────────────────────────────────────────────────────
#
# The spec attaches completions to resource templates and prompts only — the
# ref is a ResourceTemplateReference or a PromptReference, never a tool — so
# this could not exist before the templates above did. It is what makes the
# corpus browsable: a client can type "nova://shard/proj" and be offered the
# real ids rather than having to search first.

#: Completion cap. Enough to be useful, small enough that a bare prefix does
#: not ship the whole corpus.
_COMPLETION_LIMIT = 50


def _shard_id_completions(prefix: str) -> list[str]:
    """Shard ids by prefix, out of the SQLite index.

    ``id`` is that table's primary key, so this is an index scan; the index is
    write-through from ``store.save_shard``, so it is current.
    """
    from nova_shard_db import get_nova_shard_db
    try:
        return get_nova_shard_db().ids_with_prefix(prefix, limit=_COMPLETION_LIMIT)
    except Exception:
        _logger.debug("shard id completion failed", exc_info=True)
        return []


def _wiki_slug_completions(prefix: str) -> list[str]:
    """Wiki slugs by prefix.

    The schema (the plan) and the pages on disk (what got ingested) can
    legitimately disagree, so both are offered: completing only from the schema
    would hide a page, and completing only from disk would hide a planned page
    a client may want to write.
    """
    slugs = {spec.slug for spec in load_wiki_schema()}
    slugs.update(page.slug for page in all_wiki_pages())
    return sorted(s for s in slugs if s.startswith(prefix))[:_COMPLETION_LIMIT]


@mcp.completion()
async def complete_argument(ref, argument, context):
    """Complete a resource-template or prompt argument."""
    name = argument.name
    prefix = argument.value or ""

    if isinstance(ref, ResourceTemplateReference):
        if ref.uri == "nova://shard/{shard_id}" and name == "shard_id":
            return Completion(values=_shard_id_completions(prefix))
        if ref.uri == "nova://wiki/{slug}" and name == "slug":
            return Completion(values=_wiki_slug_completions(prefix))
        return None

    if isinstance(ref, PromptReference):
        if name in ("shard_id", "shard_ids"):
            return Completion(values=_shard_id_completions(prefix))
        if name == "slug":
            return Completion(values=_wiki_slug_completions(prefix))
        if name == "category":
            categories = sorted({spec.category for spec in load_wiki_schema()})
            return Completion(values=[c for c in categories if c.startswith(prefix)])
    return None


# ── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    mcp.run()
