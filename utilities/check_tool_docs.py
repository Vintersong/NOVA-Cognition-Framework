#!/usr/bin/env python3
"""Lightweight docs consistency checks for exported MCP tools."""

from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
NOVA_SERVER = REPO_ROOT / "mcp" / "nova_server.py"
SCHEMAS = REPO_ROOT / "mcp" / "schemas.py"
SKILL = REPO_ROOT / "mcp" / "SKILL.md"
CLAUDE = REPO_ROOT / "CLAUDE.md"


def _extract_tools_from_nova_server(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_ALL_TOOL_NAMES":
                    if isinstance(node.value, (ast.Tuple, ast.List)):
                        values: list[str] = []
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                values.append(elt.value)
                        return values
    raise RuntimeError("Could not locate _ALL_TOOL_NAMES in mcp/nova_server.py")


def _assert_count_phrase(path: Path, expected_count: int) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if str(expected_count) not in text:
        return [f"{path}: missing expected tool count '{expected_count}'"]
    return []


def _assert_tool_mentions(path: Path, tools: list[str]) -> list[str]:
    text = path.read_text(encoding="utf-8")
    missing = [tool for tool in tools if tool not in text]
    if not missing:
        return []
    return [f"{path}: missing tool mentions: {', '.join(missing)}"]


def main() -> int:
    tools = _extract_tools_from_nova_server(NOVA_SERVER)
    expected_count = len(tools)
    errors: list[str] = []

    errors.extend(_assert_count_phrase(SCHEMAS, expected_count))
    errors.extend(_assert_count_phrase(SKILL, expected_count))
    errors.extend(_assert_count_phrase(CLAUDE, expected_count))
    errors.extend(_assert_tool_mentions(CLAUDE, tools))

    if errors:
        print("Tool docs consistency check failed:")
        for err in errors:
            print(f" - {err}")
        return 1

    print(f"Tool docs consistency check passed ({expected_count} tools).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
