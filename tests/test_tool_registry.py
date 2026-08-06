"""
Tool registry invariants.

The registry at ``mcp/tool_registry.py`` is the single source of truth for
NOVA's MCP tool metadata. These tests guard:

  * No file uses bare ``@mcp.tool`` — every registration must go through
    ``@nova_tool`` so the registry-membership check fires at import time.
  * Every capability and category in the registry is one of the documented sets.
  * Irreversible flag never applies to read-only capabilities.
  * ``resolve_capability`` hard-fails on unknown tools (no default fallback).
  * The audit log's shard-tool set still matches the historical hardcoded one,
    so behaviour didn't drift during the refactor.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import tool_registry
from capability_gate import CapabilityDenied, resolve_capability


REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_DIR = REPO_ROOT / "mcp"

TOOL_MODULES = [
    MCP_DIR / "nova_server.py",
    MCP_DIR / "facts.py",
    MCP_DIR / "wiki_tools.py",
    MCP_DIR / "nidhogg.py",
    MCP_DIR / "evolve.py",
    MCP_DIR / "code_index.py",
    MCP_DIR / "Gemini" / "gemini_mcp.py",
]


def _decorator_targets(path: Path) -> list[tuple[str, int]]:
    """Return [(decorator_repr, line)] for every decorator call in *path*."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            func = dec.func
            # @mcp.tool(...) — bare attribute call we want to forbid
            if isinstance(func, ast.Attribute) and func.attr == "tool":
                if isinstance(func.value, ast.Name) and func.value.id == "mcp":
                    out.append(("@mcp.tool(...)", dec.lineno))
    return out


def test_no_bare_mcp_tool_decorator_remains():
    """Every @mcp.tool must be routed through @nova_tool from tool_registry."""
    offenders: list[str] = []
    for module in TOOL_MODULES:
        for rep, lineno in _decorator_targets(module):
            offenders.append(f"{module.relative_to(REPO_ROOT)}:{lineno} {rep}")
    assert not offenders, (
        "Bare @mcp.tool decorators bypass the registry-membership check. "
        "Replace with @nova_tool(mcp, name=...):\n  " + "\n  ".join(offenders)
    )


def test_registry_has_41_tools():
    """Sanity floor — catches accidental deletions."""
    assert len(tool_registry._REGISTRY) == 41


def test_registry_capabilities_are_known():
    for spec in tool_registry._REGISTRY.values():
        assert spec.capability in tool_registry._KNOWN_CAPABILITIES, (
            f"{spec.name}: capability {spec.capability!r} not in {sorted(tool_registry._KNOWN_CAPABILITIES)}"
        )


def test_registry_categories_are_known():
    for spec in tool_registry._REGISTRY.values():
        assert spec.category in tool_registry._KNOWN_CATEGORIES


def test_irreversible_never_applies_to_reads():
    for spec in tool_registry._REGISTRY.values():
        if spec.irreversible:
            assert spec.capability != "fs.read", (
                f"{spec.name}: irreversible cannot pair with fs.read"
            )


def test_resolve_capability_hard_fails_on_unknown():
    with pytest.raises(CapabilityDenied):
        resolve_capability("not_a_real_tool")


def test_resolve_capability_passes_for_every_registry_entry():
    for name in tool_registry.all_names():
        cap, irrev = resolve_capability(name)
        spec = tool_registry.get(name)
        assert cap == spec.capability
        assert irrev == spec.irreversible


def test_capability_map_matches_registry():
    """capability_map() returns the same tuples resolve_capability uses."""
    cap_map = tool_registry.capability_map()
    assert set(cap_map.keys()) == set(tool_registry.all_names())
    for name, (cap, irrev) in cap_map.items():
        spec = tool_registry.get(name)
        assert cap == spec.capability
        assert irrev == spec.irreversible


def test_shard_tools_set_matches_audit_log_filter():
    """Audit log's _SHARD_TOOLS targets shard IDs, so it must include
    shard-category irreversibles, nova_graph_relate (graph category,
    irreversible, target is source shard ID), and the nidhogg tools
    (nidhogg category, irreversible, target is a matched shard ID)."""
    from audit_log import _SHARD_TOOL_NAMES
    expected = {
        "nova_shard_archive",
        "nova_shard_forget",
        "nova_shard_consolidate",
        "nova_graph_relate",
        "nidhogg_ingest",
        "nidhogg_scan",
    }
    assert set(_SHARD_TOOL_NAMES) == expected


def test_nova_graph_relate_is_irreversible():
    """corroborated_by raises shard confidence — must be audit-logged."""
    spec = tool_registry.get("nova_graph_relate")
    assert spec.irreversible is True
    assert spec.category == "graph"


def test_tools_by_category_covers_registry():
    """Categories partition the registry — no orphans, no duplicates by name."""
    seen: set[str] = set()
    for cat in tool_registry._KNOWN_CATEGORIES:
        for spec in tool_registry.tools_by_category(cat):
            assert spec.name not in seen, f"{spec.name} in two categories"
            seen.add(spec.name)
    assert seen == set(tool_registry.all_names())


def test_invalid_toolspec_capability_rejected():
    with pytest.raises(ValueError):
        tool_registry.ToolSpec(name="x", capability="totally.fake", category="shard")


def test_invalid_toolspec_category_rejected():
    with pytest.raises(ValueError):
        tool_registry.ToolSpec(name="x", capability="fs.read", category="bogus")


def test_invalid_toolspec_irreversible_read_rejected():
    with pytest.raises(ValueError):
        tool_registry.ToolSpec(name="x", capability="fs.read", category="shard", irreversible=True)
