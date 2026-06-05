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
    "wiki", "facts", "nidhogg", "evolve", "gemini", "retrieval",
    "calibrate",
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
    # ── Shard core (16) ──────────────────────────────────────────────────
    _spec("nova_shard_interact",    "fs.read",        "shard"),
    _spec("nova_shard_create",      "fs.write.rev",   "shard"),
    _spec("nova_shard_update",      "fs.write.rev",   "shard"),
    _spec("nova_shard_validate",    "fs.write.rev",   "shard"),
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
    # nova_graph_relate is the only sanctioned path to raise shard confidence
    # (via corroborated_by). Marked irreversible so every call generates an
    # audit request_id and a matching log_executed record.
    _spec("nova_graph_relate",      "fs.write.rev",   "graph", irreversible=True),
    # ── Session (3) ──────────────────────────────────────────────────────
    _spec("nova_session_flush",     "fs.write.rev",   "session"),
    _spec("nova_session_load",      "fs.write.rev",   "session"),
    _spec("nova_session_list",      "fs.read",        "session"),
    # ── Forgemaster (2) ──────────────────────────────────────────────────
    _spec("nova_forgemaster_sprint", "spawn.proc",    "forgemaster", irreversible=True),
    _spec("nova_cache_prewarm",      "net.egress",    "forgemaster"),
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
    # Nidhogg does no network I/O — it reads local files, embeds them with the
    # local MiniLM model, and appends provenance blocks to matched shards. Both
    # ingest and scan irreversibly mutate shards (scan is just batch ingest), so
    # both are fs.write.irrev. nidhogg_status is a read-only manifest view.
    _spec("nidhogg_ingest",         "fs.write.irrev", "nidhogg", irreversible=True),
    _spec("nidhogg_scan",           "fs.write.irrev", "nidhogg", irreversible=True),
    _spec("nidhogg_status",         "fs.read",        "nidhogg"),
    # ── Evolve (1) ───────────────────────────────────────────────────────
    _spec("nova_evolve",            "memory.write",   "evolve", irreversible=True),
    # ── Gemini (2) ───────────────────────────────────────────────────────
    # The Gemini tools spawn no process — they call the Gemini API over the
    # network. execute_ticket always egresses the prompt/context to Google and
    # may write generated output; the egress can't be unsent → net.egress +
    # irreversible. load_file only reads a local repo file → fs.read.
    _spec("gemini_execute_ticket",  "net.egress",     "gemini", irreversible=True),
    _spec("gemini_load_file",       "fs.read",        "gemini"),
    # ── External retrieval deliberation (1) ──────────────────────────────
    # net.egress: fires Anthropic API calls (Haiku + Sonnet/Opus).
    # Reversible: shard writes can be undone via nova_shard_archive/forget.
    _spec("nova_external_retrieval", "net.egress",    "retrieval"),
    # ── Calibrate (1) ────────────────────────────────────────────────────
    # Read-only: only reads nova_usage.jsonl + forgemaster run logs and
    # reports findings. Suggested thresholds are applied by hand via env var.
    _spec("nova_calibrate_routing",  "fs.read",       "calibrate"),
    # ── HUGINN pre-filter (1) ─────────────────────────────────────────────
    # Read-only: keyword + confidence pre-filter over the shard index.
    # Returns a small candidate list ready to paste into a HUGINN agent prompt.
    _spec("nova_huginn_candidates",  "fs.read",       "shard"),
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
