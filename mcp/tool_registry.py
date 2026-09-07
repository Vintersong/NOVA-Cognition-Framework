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
    "calibrate", "code_index",
})


@dataclass(frozen=True)
class ToolSpec:
    """Declarative metadata for one MCP tool."""
    name: str
    capability: str
    irreversible: bool = False
    category: str = "shard"
    description: str = ""  # short one-liner; function docstring is the long form
    title: str = ""        # human-readable display name published as the MCP tool title

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


def _spec(
    name: str,
    capability: str,
    category: str,
    irreversible: bool = False,
    title: str = "",
) -> tuple[str, ToolSpec]:
    return name, ToolSpec(
        name=name, capability=capability, category=category,
        irreversible=irreversible, title=title,
    )


# Single source of truth. Order is preserved by dict insertion → maps to the
# order tools appear in `_ALL_TOOL_NAMES` (which CLAUDE.md mirrors).
_REGISTRY: dict[str, ToolSpec] = dict([
    # ── Shard core (16) ──────────────────────────────────────────────────
    _spec("nova_shard_interact",    "fs.read",        "shard", title="Load Shards into Context"),
    _spec("nova_shard_create",      "fs.write.rev",   "shard", title="Create Shard"),
    _spec("nova_shard_update",      "fs.write.rev",   "shard", title="Append Turn to Shard"),
    _spec("nova_shard_validate",    "fs.write.rev",   "shard", title="Record Validation Event"),
    _spec("nova_shard_search",      "fs.read",        "shard", title="Search Shards"),
    _spec("nova_shard_query_state", "fs.read",        "shard", title="Query Shard State Vector"),
    _spec("nova_obsidian_export",   "fs.write.rev",   "shard", title="Export Shards to Obsidian"),
    _spec("nova_shard_index",       "fs.read",        "shard", title="Browse Shard Index"),
    _spec("nova_shard_summary",     "fs.read",        "shard", title="Browse Shards with Synopsis"),
    _spec("nova_shard_list",        "fs.read",        "shard", title="List Shards (legacy dump)"),
    _spec("nova_shard_get",         "fs.read",        "shard", title="Read Shard"),
    _spec("nova_shard_get_full",    "fs.read",        "shard", title="Read Shard Body"),
    _spec("nova_shard_merge",       "fs.write.rev",   "shard", title="Merge Shards"),
    _spec("nova_shard_archive",     "fs.write.irrev", "shard", irreversible=True, title="Archive Shard"),
    _spec("nova_shard_forget",      "fs.write.irrev", "shard", irreversible=True, title="Forget Shard"),
    _spec("nova_shard_consolidate", "fs.write.irrev", "shard", irreversible=True, title="Run Maintenance Cycle"),
    # ── Graph (2) ────────────────────────────────────────────────────────
    _spec("nova_graph_query",       "fs.read",        "graph", title="Query Knowledge Graph"),
    # nova_graph_relate is the only sanctioned path to raise shard confidence
    # (via corroborated_by). Marked irreversible so every call generates an
    # audit request_id and a matching log_executed record.
    _spec("nova_graph_relate",      "fs.write.rev",   "graph", irreversible=True, title="Relate Two Shards"),
    # ── Session (3) ──────────────────────────────────────────────────────
    _spec("nova_session_flush",     "fs.write.rev",   "session", title="Flush Session to Disk"),
    _spec("nova_session_load",      "fs.write.rev",   "session", title="Load Stored Session"),
    _spec("nova_session_list",      "fs.read",        "session", title="List Stored Sessions"),
    # ── Forgemaster (2) ──────────────────────────────────────────────────
    _spec("nova_forgemaster_sprint", "spawn.proc",    "forgemaster", irreversible=True, title="Run Forgemaster Sprint"),
    _spec("nova_cache_prewarm",      "net.egress",    "forgemaster", title="Pre-warm Prompt Cache"),
    # ── Wiki (6) ─────────────────────────────────────────────────────────
    # action="add"/"remove" call save_wiki_schema() — this is a write path.
    # Additive/removable spec entries only; wiki files are never deleted.
    _spec("nova_wiki_schema",       "fs.write.rev",   "wiki", title="View or Edit Wiki Taxonomy"),
    _spec("nova_wiki_ingest",       "fs.write.rev",   "wiki", title="Ingest Document into Wiki"),
    _spec("nova_wiki_query",        "fs.read",        "wiki", title="Search Wiki"),
    _spec("nova_wiki_get",          "fs.read",        "wiki", title="Read Wiki Page"),
    _spec("nova_wiki_list",         "fs.read",        "wiki", title="List Wiki Pages"),
    _spec("nova_wiki_lint",         "fs.write.rev",   "wiki", title="Lint Wiki"),
    # ── Facts (2) ────────────────────────────────────────────────────────
    _spec("nova_facts_search",      "fs.read",        "facts", title="Search Facts Corpus"),
    _spec("nova_facts_rebuild",     "fs.write.rev",   "facts", title="Rebuild Facts Index"),
    # ── Nidhogg (3) ──────────────────────────────────────────────────────
    # Nidhogg does no network I/O — it reads local files, embeds them with the
    # local MiniLM model, and appends provenance blocks to matched shards. Both
    # ingest and scan irreversibly mutate shards (scan is just batch ingest), so
    # both are fs.write.irrev. nidhogg_status is a read-only manifest view.
    _spec("nidhogg_ingest",         "fs.write.irrev", "nidhogg", irreversible=True, title="Ingest Document (Nidhogg)"),
    _spec("nidhogg_scan",           "fs.write.irrev", "nidhogg", irreversible=True, title="Scan Intake Directory"),
    _spec("nidhogg_status",         "fs.read",        "nidhogg", title="Nidhogg Status"),
    # ── Evolve (1) ───────────────────────────────────────────────────────
    _spec("nova_evolve",            "memory.write",   "evolve", irreversible=True, title="Run Self-Improvement Loop"),
    # ── Gemini (2) ───────────────────────────────────────────────────────
    # The Gemini tools spawn no process — they call the Gemini API over the
    # network. execute_ticket always egresses the prompt/context to Google and
    # may write generated output; the egress can't be unsent → net.egress +
    # irreversible. load_file only reads a local repo file → fs.read.
    _spec("gemini_execute_ticket",  "net.egress",     "gemini", irreversible=True, title="Execute Ticket via Gemini"),
    _spec("gemini_load_file",       "fs.read",        "gemini", title="Load File as Context"),
    # ── External retrieval deliberation (1) ──────────────────────────────
    # net.egress: fires Anthropic API calls (Haiku + Sonnet/Opus).
    # Reversible: shard writes can be undone via nova_shard_archive/forget.
    _spec("nova_external_retrieval", "net.egress",    "retrieval", title="External Retrieval Deliberation"),
    # ── Calibrate (1) ────────────────────────────────────────────────────
    # Read-only: only reads nova_usage.jsonl + forgemaster run logs and
    # reports findings. Suggested thresholds are applied by hand via env var.
    _spec("nova_calibrate_routing",  "fs.read",       "calibrate", title="Analyse Routing Thresholds"),
    # ── HUGINN pre-filter (1) ─────────────────────────────────────────────
    # Read-only: keyword + confidence pre-filter over the shard index.
    # Returns a small candidate list ready to paste into a HUGINN agent prompt.
    _spec("nova_huginn_candidates",  "fs.read",       "shard", title="HUGINN Candidate Pre-filter"),
    # ── Code index (1) ───────────────────────────────────────────────────
    # Read-only: cosine search over a locally-embedded AST chunk manifest of
    # mcp/**/*.py, kept warm by a SESSION_START background refresh. No shard
    # or network I/O.
    _spec("nova_code_search",        "fs.read",       "code_index", title="Search NOVA Source Code"),
])


# ── MCP tool annotations ─────────────────────────────────────────────────────
#
# The spec's tool annotations (readOnlyHint / destructiveHint / idempotentHint /
# openWorldHint) are the hints a client uses to decide what it may auto-approve.
# NOVA already states those same facts as capability tags, so the annotations are
# derived from the registry rather than hand-written per tool: 41 tools stay
# consistent for free, and re-tagging a capability can't leave a stale hint on
# the wire.
#
# `irreversible` is deliberately NOT the source of destructiveHint. On a
# fs.write.rev tool it is an audit-routing flag meaning "always mint a
# request_id" (see nova_graph_relate), not a claim that the tool destroys
# anything. Capability class alone decides destructiveness.

#: Capability classes whose tools destroy or irreversibly alter state.
#:
#: This single set drives two things that must never disagree: the
#: ``destructiveHint`` a client reads to decide what it may auto-approve, and
#: whether ``capability_gate`` asks the operator before letting the call run.
#: Deriving both from here means a client can never be told a tool is safe while
#: the gate treats it as dangerous, or the reverse.
DESTRUCTIVE_CAPABILITIES: frozenset[str] = frozenset({
    "fs.write.irrev",  # shard archive/forget/consolidate, nidhogg ingest/scan
    "memory.write",    # nova_evolve rewrites NOVA's own prompts and shards
    "spawn.proc",      # nova_forgemaster_sprint egresses and writes output
})

# Hints that follow from the capability class alone. destructiveHint is absent
# on purpose — it is computed from DESTRUCTIVE_CAPABILITIES above so the two
# cannot drift apart.
_ANNOTATIONS_BY_CAPABILITY: dict[str, dict[str, bool]] = {
    # read-only, closed world
    "fs.read":        {"readOnlyHint": True,  "idempotentHint": True,
                       "openWorldHint": False},
    # additive/undoable local writes
    "fs.write.rev":   {"readOnlyHint": False, "idempotentHint": False,
                       "openWorldHint": False},
    "fs.write.irrev": {"readOnlyHint": False, "idempotentHint": False,
                       "openWorldHint": False},
    "memory.write":   {"readOnlyHint": False, "idempotentHint": False,
                       "openWorldHint": False},
    # talks to a third-party API: open world, but sending a request is not
    # itself a destructive update to the local environment
    "net.egress":     {"readOnlyHint": False, "idempotentHint": False,
                       "openWorldHint": True},
    "spawn.proc":     {"readOnlyHint": False, "idempotentHint": False,
                       "openWorldHint": True},
}

# A capability with no annotation mapping would silently publish nothing.
_unmapped = _KNOWN_CAPABILITIES - _ANNOTATIONS_BY_CAPABILITY.keys()
if _unmapped:
    raise RuntimeError(
        f"capabilities missing from _ANNOTATIONS_BY_CAPABILITY: {sorted(_unmapped)}"
    )

_unknown_destructive = DESTRUCTIVE_CAPABILITIES - _KNOWN_CAPABILITIES
if _unknown_destructive:
    raise RuntimeError(
        f"DESTRUCTIVE_CAPABILITIES names unknown capabilities: {sorted(_unknown_destructive)}"
    )


def annotations_for(name: str) -> dict[str, bool]:
    """MCP tool annotations for *name*, derived from its capability tag."""
    capability = _REGISTRY[name].capability
    annotations = dict(_ANNOTATIONS_BY_CAPABILITY[capability])
    annotations["destructiveHint"] = capability in DESTRUCTIVE_CAPABILITIES
    return annotations


def requires_approval(name: str) -> bool:
    """True if *name* must be confirmed by a human before it runs.

    Exactly the tools published with ``destructiveHint`` — see
    ``DESTRUCTIVE_CAPABILITIES``. Notably excludes ``nova_graph_relate``, whose
    ``irreversible`` flag exists to route it through the audit log rather than to
    claim it destroys anything.
    """
    return _REGISTRY[name].capability in DESTRUCTIVE_CAPABILITIES


def destructive_tools() -> frozenset[str]:
    """Names of every tool that requires human approval."""
    return frozenset(
        name for name, spec in _REGISTRY.items()
        if spec.capability in DESTRUCTIVE_CAPABILITIES
    )


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
    membership at import time and publishes the registry's own metadata:
    ``ToolSpec.title`` as the MCP tool title, and capability-derived tool
    annotations (see ``annotations_for``). A call site may still pass
    ``title=`` or ``annotations={...}`` to override, key by key.

    Raises ``RuntimeError`` if *name* is not in ``_REGISTRY`` — adding a new
    tool requires editing this module first.
    """
    if name not in _REGISTRY:
        raise RuntimeError(
            f"Tool '{name}' is not declared in mcp/tool_registry.py. "
            f"Add a ToolSpec entry to _REGISTRY before registering."
        )
    spec = _REGISTRY[name]

    # Registry-derived defaults; a call site may override individual keys.
    annotations = annotations_for(name)
    annotations.update(mcp_kwargs.pop("annotations", None) or {})
    if spec.title:
        mcp_kwargs.setdefault("title", spec.title)

    return mcp.tool(name=name, annotations=annotations, **mcp_kwargs)
