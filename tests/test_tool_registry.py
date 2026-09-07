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
from pydantic import BaseModel

import tool_registry
from capability_gate import CapabilityDenied, resolve_capability


REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_DIR = REPO_ROOT / "mcp"

def _all_mcp_sources() -> list[Path]:
    """Every Python source under mcp/.

    Derived rather than hardcoded: the previous explicit list had gone stale and
    omitted shard_tools.py (16 tools), graph_tools.py, session_tools.py,
    forgemaster_tools.py, huginn_tools.py, calibrate.py and
    external_retrieval.py — so a bare @mcp.tool in the largest tool module was
    undetectable.
    """
    return sorted(p for p in MCP_DIR.rglob("*.py") if "__pycache__" not in p.parts)


TOOL_MODULES = _all_mcp_sources()


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


def test_scan_covers_every_module_that_registers_tools():
    """Guards the glob itself: if it stopped matching, the bare-decorator scan
    below would pass vacuously."""
    scanned = {p.name for p in TOOL_MODULES}
    must_cover = {
        "nova_server.py", "shard_tools.py", "graph_tools.py", "session_tools.py",
        "forgemaster_tools.py", "wiki_tools.py", "facts.py", "nidhogg.py",
        "evolve.py", "code_index.py", "huginn_tools.py", "calibrate.py",
        "external_retrieval.py", "gemini_mcp.py",
    }
    assert must_cover <= scanned, f"not scanned: {sorted(must_cover - scanned)}"


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


# ── MCP tool annotations ─────────────────────────────────────────────────────
#
# Annotations are derived from each tool's capability tag rather than written by
# hand, so these guard the derivation itself and the fact that it reaches the
# wire. Before this, 39 of 41 tools published no annotations at all and a client
# could not tell nova_shard_get from nova_shard_forget.

HINT_KEYS = {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}


class _WireIn(BaseModel):
    """Module-scope so the SDK's get_type_hints() call can resolve it — this
    file uses `from __future__ import annotations`."""
    q: str = ""


def test_every_capability_has_an_annotation_mapping():
    unmapped = tool_registry._KNOWN_CAPABILITIES - tool_registry._ANNOTATIONS_BY_CAPABILITY.keys()
    assert not unmapped, f"capabilities with no annotations: {sorted(unmapped)}"


def test_every_tool_publishes_all_four_hints():
    for name in tool_registry.all_names():
        assert set(tool_registry.annotations_for(name)) == HINT_KEYS, name


def test_every_tool_has_a_title():
    missing = [n for n in tool_registry.all_names() if not tool_registry.get(n).title]
    assert not missing, f"tools with no title: {missing}"


def test_read_capabilities_are_annotated_read_only():
    for name in tool_registry.all_names():
        spec = tool_registry.get(name)
        ann = tool_registry.annotations_for(name)
        assert ann["readOnlyHint"] is (spec.capability == "fs.read"), name


def test_irreversible_capabilities_are_annotated_destructive():
    destructive_caps = {"fs.write.irrev", "memory.write", "spawn.proc"}
    for name in tool_registry.all_names():
        spec = tool_registry.get(name)
        ann = tool_registry.annotations_for(name)
        assert ann["destructiveHint"] is (spec.capability in destructive_caps), name


def test_reversible_write_is_not_destructive_despite_irreversible_flag():
    """`irreversible` on an fs.write.rev tool is an audit-routing flag, not a
    destructiveness claim — nova_graph_relate sets it purely to mint a
    request_id, and must not be advertised as destructive."""
    spec = tool_registry.get("nova_graph_relate")
    assert spec.irreversible is True
    assert spec.capability == "fs.write.rev"
    assert tool_registry.annotations_for("nova_graph_relate")["destructiveHint"] is False


def test_read_and_destructive_shard_tools_are_distinguishable():
    get = tool_registry.annotations_for("nova_shard_get")
    forget = tool_registry.annotations_for("nova_shard_forget")
    assert get["readOnlyHint"] is True and get["destructiveHint"] is False
    assert forget["readOnlyHint"] is False and forget["destructiveHint"] is True


def test_wiki_schema_is_declared_a_write():
    """action="add"/"remove" call save_wiki_schema(); tagging it fs.read let the
    capability gate treat a taxonomy mutation as a read."""
    assert tool_registry.get("nova_wiki_schema").capability == "fs.write.rev"


def test_annotations_and_title_reach_the_wire():
    """End-to-end through a real MCPServer instance: what a client sees in
    tools/list must carry the registry's title and derived annotations."""
    import asyncio

    pytest.importorskip("mcp.server.mcpserver", reason="MCP SDK not installed in this environment")
    from mcp.server.mcpserver import MCPServer

    mcp = MCPServer("test")

    @tool_registry.nova_tool(mcp, name="nova_shard_forget")
    async def _forget(params: _WireIn) -> str:
        return ""

    @tool_registry.nova_tool(mcp, name="nova_shard_get")
    async def _get(params: _WireIn) -> str:
        return ""

    published = {
        t["name"]: t
        for t in (x.model_dump(by_alias=True, mode="json") for x in asyncio.run(mcp.list_tools()))
    }

    forget = published["nova_shard_forget"]
    assert forget["title"] == "Forget Shard"
    assert forget["annotations"]["readOnlyHint"] is False
    assert forget["annotations"]["destructiveHint"] is True

    get = published["nova_shard_get"]
    assert get["title"] == "Read Shard"
    assert get["annotations"]["readOnlyHint"] is True
    assert get["annotations"]["destructiveHint"] is False


def test_call_site_can_override_derived_annotations():
    pytest.importorskip("mcp.server.mcpserver", reason="MCP SDK not installed in this environment")
    import asyncio
    from mcp.server.mcpserver import MCPServer

    mcp = MCPServer("test")

    @tool_registry.nova_tool(
        mcp, name="nova_shard_get", title="Custom", annotations={"idempotentHint": False},
    )
    async def _get(params: _WireIn) -> str:
        return ""

    tool = {
        t["name"]: t
        for t in (x.model_dump(by_alias=True, mode="json") for x in asyncio.run(mcp.list_tools()))
    }["nova_shard_get"]
    assert tool["title"] == "Custom"
    assert tool["annotations"]["idempotentHint"] is False
    # untouched keys still come from the registry
    assert tool["annotations"]["readOnlyHint"] is True
