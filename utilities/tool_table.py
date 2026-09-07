"""
tool_table.py — render NOVA's tool inventory as a markdown table.

CLAUDE.md used to carry a hand-maintained table, and about a dozen of its rows
had drifted into describing something the tool does not do ("Rebuild or inspect
the shard index" for a browse tool; "Inspect" for a write path). Generating it
removes the hand-maintained copy. The chain is:

    handler docstring
      -> live server            (utilities/dump_tool_manifest.py --write)
      -> tests/golden/tool_manifest.json   (verified by tests/test_tool_manifest.py)
      -> the table in CLAUDE.md (verified by utilities/check_tool_docs.py)

This module deliberately reads only the committed manifest and the registry, so
neither the docs gate nor CI has to boot the server and the embedding stack to
check the table.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "tests" / "golden" / "tool_manifest.json"

BEGIN_MARKER = "<!-- BEGIN GENERATED TOOL TABLE -->"
END_MARKER = "<!-- END GENERATED TOOL TABLE -->"


def load_manifest() -> dict:
    """The committed golden manifest — cheap, and test-verified against the
    live server, so callers need not boot the whole embedding stack."""
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def render_tool_table(manifest: dict) -> str:
    """Render the tool inventory as a markdown table."""
    sys.path.insert(0, str(REPO_ROOT / "mcp"))
    from tool_registry import requires_approval

    rows = ["| Tool | Title | Purpose |", "|---|---|---|"]
    for tool in manifest["tools"]:
        name = tool["name"]
        purpose = tool["summary"].rstrip(".")
        marks = []
        if (tool.get("annotations") or {}).get("readOnlyHint"):
            marks.append("read-only")
        if requires_approval(name):
            marks.append("**asks for approval**")
        suffix = f" _({', '.join(marks)})_" if marks else ""
        rows.append(f"| `{name}` | {tool['title']} | {purpose}.{suffix} |")
    return "\n".join(rows)


def splice_table(path: Path, table: str) -> bool:
    """Replace the marked block in *path*. Returns True if the file changed."""
    text = path.read_text(encoding="utf-8")
    if BEGIN_MARKER not in text or END_MARKER not in text:
        raise SystemExit(
            f"{path} has no generated-table markers. Add:\n"
            f"  {BEGIN_MARKER}\n  {END_MARKER}"
        )
    head, rest = text.split(BEGIN_MARKER, 1)
    _, tail = rest.split(END_MARKER, 1)
    updated = f"{head}{BEGIN_MARKER}\n{table}\n{END_MARKER}{tail}"
    if updated == text:
        return False
    path.write_text(updated, encoding="utf-8")
    return True
