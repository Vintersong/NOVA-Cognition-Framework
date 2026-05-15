#!/usr/bin/env python3
"""Lightweight docs consistency checks for exported MCP tools."""

from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = REPO_ROOT / "mcp" / "schemas.py"
SKILL = REPO_ROOT / "mcp" / "SKILL.md"
CLAUDE = REPO_ROOT / "CLAUDE.md"


def _extract_tools() -> list[str]:
    """Pull the canonical tool list from mcp/tool_registry.py."""
    sys.path.insert(0, str(REPO_ROOT / "mcp"))
    from tool_registry import all_names
    return list(all_names())


def _assert_count_phrase(path: Path, expected_count: int) -> list[str]:
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"(?i)(\btools?\b[^\n]{{0,40}}\b{expected_count}\b|\b{expected_count}\b[^\n]{{0,40}}\btools?\b)"
    )
    if pattern.search(text) is None:
        return [f"{path}: missing expected tool count '{expected_count}'"]
    return []


def _assert_tool_mentions(path: Path, tools: list[str]) -> list[str]:
    text = path.read_text(encoding="utf-8")
    missing = [tool for tool in tools if tool not in text]
    if not missing:
        return []
    return [f"{path}: missing tool mentions: {', '.join(missing)}"]


def main() -> int:
    tools = _extract_tools()
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
