"""
tool_registry.py — single source of truth for NOVA MCP tool metadata.

Every ``@mcp.tool`` registration in the codebase routes through ``@nova_tool``,
which validates the tool name against ``_REGISTRY`` at import time. Adding a
new tool requires adding a ``ToolSpec`` entry here first, or imports fail.

Downstream consumers all read from ``_REGISTRY`` via the accessors below — no
parallel inventory anywhere:

  * ``mcp/nova_server.py:_ALL_TOOL_NAMES``     ← ``all_names()``
  * ``mcp/capability_gate.py:_CAPABILITY_MAP`` ← ``capability_map()``
  * ``mcp/audit_log.py:AuditLog._SHARD_TOOLS`` ← shard-category irreversibles
  * ``utilities/check_tool_docs.py``           ← ``all_names()``
  * A future A2A Agent Card                    ← ``tools_by_category()``
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


# Valid capability tags — kept in sync with the skill manifest layer.
_KNOWN_CAPABILITIES: frozenset[str] = frozenset({
    "fs.read",
    "fs.write.rev",
    "fs.write.irrev",
    "memory.write",
    "net.egress",
    "spawn.proc",
})

# Valid categories. Each maps 1:1 to a module that registers tools.
_KNOWN_CATEGORIES: frozenset[str] = frozenset({
    "shard", "graph", "session", "forgemaster",
    "wiki", "facts", "nidhogg", "evolve", "gemini",
})


@dataclass(frozen=True)
class ToolSpec:
    """Declarative metadata for one MCP tool."""
    name: str
    capability: str
    irreversible: bool = False
    category: str = "shard"
    description: str = ""  # short one-liner; function docstring is the long form

    def __post_init__(self) -> None:
        if self.capability not in _KNOWN_CAPABILITIES:
            raise ValueError(
                f"ToolSpec '{self.name}': unknown capability '{self.capability}'. "
                f"Valid: {sorted(_KNOWN_CAPABILITIES)}"
            )
        if self.category not in _KNOWN_CATEGORIES:
            raise ValueError(
                f"ToolSpec '{self.name}': unknown category '{self.category}'. "
                f"Valid: {sorted(_KNOWN_CATEGORIES)}"
            )
        if self.irreversible and self.capability == "fs.read":
            raise ValueError(
                f"ToolSpec '{self.name}': fs.read cannot be irreversible"
            )


def _spec(name: str, capability: str, category: str, irreversible: bool = False) -> tuple[str, ToolSpec]:
    return name, ToolSpec(name=name, capability=capability, category=category, irreversible=irreversible)


# Single source of truth. Order is preserved by dict insertion → maps to the
# order tools appear in `_ALL_TOOL_NAMES` (which CLAUDE.md mirrors).
_REGISTRY: dict[str, ToolSpec] = dict([
    # ── Shard core (15) ──────────────────────────────────────────────────
    _spec("nova_shard_interact",    "fs.read",        "shard"),
    _spec("nova_shard_create",      "fs.write.rev",   "shard"),
    _spec("nova_shard_update",      "fs.write.rev",   "shard"),
    _spec("nova_shard_search",      "fs.read",        "shard"),
    _spec("nova_shard_query_state", "fs.read",        "shard"),
    _spec("nova_obsidian_export",   "fs.write.rev",   "shard"),
    _spec("nova_shard_index",       "fs.read",        "shard"),
    _spec("nova_shard_summary",     "fs.read",        "shard"),
    _spec("nova_shard_list",        "fs.read",        "shard"),
    _spec("nova_shard_get",         "fs.read",        "shard"),
    _spec("nova_shard_get_full",    "fs.read",        "shard"),
    _spec("nova_shard_merge",       "fs.write.rev",   "shard"),
    _spec("nova_shard_archive",     "fs.write.irrev", "shard", irreversible=True),
    _spec("nova_shard_forget",      "fs.write.irrev", "shard", irreversible=True),
    _spec("nova_shard_consolidate", "fs.write.irrev", "shard", irreversible=True),
    # ── Graph (2) ────────────────────────────────────────────────────────
    _spec("nova_graph_query",       "fs.read",        "graph"),
    _spec("nova_graph_relate",      "fs.write.rev",   "graph"),
    # ── Session (3) ──────────────────────────────────────────────────────
    _spec("nova_session_flush",     "fs.write.rev",   "session"),
    _spec("nova_session_load",      "fs.write.rev",   "session"),
    _spec("nova_session_list",      "fs.read",        "session"),
    # ── Forgemaster (1) ──────────────────────────────────────────────────
    _spec("nova_forgemaster_sprint", "spawn.proc",    "forgemaster", irreversible=True),
    # ── Wiki (6) ─────────────────────────────────────────────────────────
    _spec("nova_wiki_schema",       "fs.read",        "wiki"),
    _spec("nova_wiki_ingest",       "fs.write.rev",   "wiki"),
    _spec("nova_wiki_query",        "fs.read",        "wiki"),
    _spec("nova_wiki_get",          "fs.read",        "wiki"),
    _spec("nova_wiki_list",         "fs.read",        "wiki"),
    _spec("nova_wiki_lint",         "fs.write.rev",   "wiki"),
    # ── Facts (2) ────────────────────────────────────────────────────────
    _spec("nova_facts_search",      "fs.read",        "facts"),
    _spec("nova_facts_rebuild",     "fs.write.rev",   "facts"),
    # ── Nidhogg (3) ──────────────────────────────────────────────────────
    _spec("nidhogg_ingest",         "net.egress",     "nidhogg", irreversible=True),
    _spec("nidhogg_scan",           "net.egress",     "nidhogg"),
    _spec("nidhogg_status",         "fs.read",        "nidhogg"),
    # ── Evolve (1) ───────────────────────────────────────────────────────
    _spec("nova_evolve",            "memory.write",   "evolve", irreversible=True),
    # ── Gemini (2) ───────────────────────────────────────────────────────
    _spec("gemini_execute_ticket",  "spawn.proc",     "gemini", irreversible=True),
    _spec("gemini_load_file",       "spawn.proc",     "gemini"),
])


# ── Public accessors ─────────────────────────────────────────────────────────

def all_names() -> tuple[str, ...]:
    """Tuple of every declared tool name, in registration order."""
    return tuple(_REGISTRY.keys())


def capability_map() -> dict[str, tuple[str, bool]]:
    """``{tool_name: (capability_tag, is_irreversible)}`` view of the registry."""
    return {name: (spec.capability, spec.irreversible) for name, spec in _REGISTRY.items()}


def irreversible_tools() -> frozenset[str]:
    """Names of every tool marked irreversible."""
    return frozenset(name for name, spec in _REGISTRY.items() if spec.irreversible)


def tools_by_category(category: str) -> tuple[ToolSpec, ...]:
    """Tools whose category matches *category* — used by future A2A Agent Card."""
    return tuple(spec for spec in _REGISTRY.values() if spec.category == category)


def get(name: str) -> ToolSpec:
    """Look up a tool by name. Raises ``KeyError`` if absent."""
    return _REGISTRY[name]


def has(name: str) -> bool:
    return name in _REGISTRY


# ── Decorator ────────────────────────────────────────────────────────────────

def nova_tool(mcp: Any, *, name: str, **mcp_kwargs: Any) -> Callable:
    """
    Drop-in replacement for ``@mcp.tool(name=...)`` that enforces registry
    membership at import time. Extra kwargs (e.g. ``annotations={...}``) are
    forwarded to ``mcp.tool`` so MCP tool annotations still flow through.

    Raises ``RuntimeError`` if *name* is not in ``_REGISTRY`` — adding a new
    tool requires editing this module first.
    """
    if name not in _REGISTRY:
        raise RuntimeError(
            f"Tool '{name}' is not declared in mcp/tool_registry.py. "
            f"Add a ToolSpec entry to _REGISTRY before registering."
        )
    return mcp.tool(name=name, **mcp_kwargs)
