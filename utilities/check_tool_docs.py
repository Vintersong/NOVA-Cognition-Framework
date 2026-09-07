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
README = REPO_ROOT / "README.md"

# Docs that must name every tool and cite the right count. README was omitted
# until now, which is how it drifted to three *different* wrong tool counts and
# lost two tools entirely without CI noticing.
TOOL_INVENTORIES = (CLAUDE, README)
COUNT_CITERS = (SCHEMAS, SKILL, CLAUDE, README)


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
    """Every registered tool must be named somewhere in *path*."""
    text = path.read_text(encoding="utf-8")
    missing = [tool for tool in tools if tool not in text]
    if not missing:
        return []
    return [f"{path}: missing tool mentions: {', '.join(missing)}"]


def _assert_generated_table_current(path: Path) -> list[str]:
    """The generated tool table in *path* must match what the manifest renders.

    CLAUDE.md's table used to be hand-maintained and about a dozen rows had
    drifted into describing something the tool does not do. It is now generated;
    this keeps it that way.
    """
    sys.path.insert(0, str(REPO_ROOT / "utilities"))
    from tool_table import BEGIN_MARKER, END_MARKER, load_manifest, render_tool_table

    text = path.read_text(encoding="utf-8")
    if BEGIN_MARKER not in text or END_MARKER not in text:
        return [f"{path}: generated-table markers are missing"]

    current = text.split(BEGIN_MARKER, 1)[1].split(END_MARKER, 1)[0].strip()
    expected = render_tool_table(load_manifest()).strip()
    if current != expected:
        return [
            f"{path}: generated tool table is stale — regenerate with\n"
            f"     python utilities/dump_tool_manifest.py --write-table "
            f"{path.relative_to(REPO_ROOT)}"
        ]
    return []


def main() -> int:
    tools = _extract_tools()
    expected_count = len(tools)
    errors: list[str] = []

    for path in COUNT_CITERS:
        errors.extend(_assert_count_phrase(path, expected_count))
    for path in TOOL_INVENTORIES:
        errors.extend(_assert_tool_mentions(path, tools))
    errors.extend(_assert_generated_table_current(CLAUDE))

    if errors:
        print("Tool docs consistency check failed:")
        for err in errors:
            print(f" - {err}")
        return 1

    print(f"Tool docs consistency check passed ({expected_count} tools).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
