"""
Permission/capability inventory drift test.

Asserts every tool that ships with NOVA is registered in both the permission
whitelist (`_ALL_TOOL_NAMES` in `mcp/nova_server.py`) and the capability map
(`_CAPABILITY_MAP` in `mcp/capability_gate.py`). Catches the failure mode
where a tool is added to one module's registration code but the security
inventory is forgotten — the same drift that left `nova_facts_*` outside
the whitelist before this audit.
"""

from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_DIR = REPO_ROOT / "mcp"

# Modules to scan for @mcp.tool(name="...") decorators. nova_server.py is
# scanned via _ALL_TOOL_NAMES rather than decorator parsing because its
# decorators are syntactically identical but already cross-checked.
TOOL_MODULES = [
    MCP_DIR / "facts.py",
    MCP_DIR / "wiki_tools.py",
    MCP_DIR / "nidhogg.py",
    MCP_DIR / "evolve.py",
    MCP_DIR / "Gemini" / "gemini_mcp.py",
]


def _all_tool_names_from_server() -> list[str]:
    tree = ast.parse((MCP_DIR / "nova_server.py").read_text(encoding="utf-8"))
    for node in tree.body:
        target = None
        value = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
            value = node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
            value = node.value
        if target != "_ALL_TOOL_NAMES" or not isinstance(value, (ast.Tuple, ast.List)):
            continue
        return [elt.value for elt in value.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)]
    raise AssertionError("Could not locate _ALL_TOOL_NAMES in mcp/nova_server.py")


def _decorated_tool_names(path: Path) -> list[str]:
    """Extract names from @mcp.tool(name="X") decorators in *path*."""
    if not path.exists():
        return []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            func = dec.func
            attr = getattr(func, "attr", None)
            if attr != "tool":
                continue
            for kw in dec.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    names.append(kw.value.value)
    return names


def _capability_map_keys() -> set[str]:
    # sys.path is set up by tests/conftest.py
    from capability_gate import _CAPABILITY_MAP
    return set(_CAPABILITY_MAP.keys())


def test_every_external_tool_in_all_tool_names():
    whitelist = set(_all_tool_names_from_server())
    for module in TOOL_MODULES:
        for name in _decorated_tool_names(module):
            assert name in whitelist, (
                f"{module.name} registers '{name}' but it is missing from "
                f"_ALL_TOOL_NAMES in mcp/nova_server.py"
            )


def test_every_whitelisted_tool_in_capability_map():
    cap_keys = _capability_map_keys()
    for name in _all_tool_names_from_server():
        assert name in cap_keys, (
            f"Tool '{name}' is in _ALL_TOOL_NAMES but missing from "
            f"_CAPABILITY_MAP in mcp/capability_gate.py — it will fall back "
            f"to the generic 'tool.invoke' capability"
        )


def test_capability_map_has_no_orphans():
    """Every entry in _CAPABILITY_MAP should correspond to a real tool."""
    cap_keys = _capability_map_keys()
    whitelist = set(_all_tool_names_from_server())
    orphans = cap_keys - whitelist
    assert not orphans, (
        f"_CAPABILITY_MAP contains entries not in _ALL_TOOL_NAMES: {sorted(orphans)}"
    )
