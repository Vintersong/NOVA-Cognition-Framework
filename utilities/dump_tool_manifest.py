#!/usr/bin/env python3
"""
dump_tool_manifest.py — snapshot what an MCP client actually sees.

Renders NOVA's ``tools/list`` and ``resources/list`` into a compact, stable
manifest. ``tests/test_tool_manifest.py`` diffs the live server against the
committed copy, so a change to a tool's title, annotations, schema shape or
output schema has to be made deliberately rather than drifting.

This is the only check that inspects the wire format. The registry tests guard
NOVA's internal metadata; this guards what leaves the process.

Regenerate after an intentional change:

    python utilities/dump_tool_manifest.py --write
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "tests" / "golden" / "tool_manifest.json"

sys.path.insert(0, str(REPO_ROOT / "mcp"))


def _schema_shape(schema: dict | None) -> dict | None:
    """Top-level shape of a JSON Schema — property names and required list.

    Deliberately not the whole schema: the full document is mostly $defs noise,
    while the property/required shape is what a model actually calls against.
    """
    if not schema:
        return None
    return {
        "properties": sorted((schema.get("properties") or {}).keys()),
        "required": sorted(schema.get("required") or []),
    }


async def build_manifest() -> dict:
    import nova_server

    mcp = nova_server.mcp
    tools = await mcp.list_tools()
    resources = await mcp.list_resources()

    tool_rows = []
    for t in sorted(tools, key=lambda x: x.name):
        ann = t.annotations
        tool_rows.append({
            "name": t.name,
            "title": t.title,
            "description_first_line": (t.description or "").strip().split("\n")[0],
            "annotations": None if ann is None else {
                "readOnlyHint": ann.readOnlyHint,
                "destructiveHint": ann.destructiveHint,
                "idempotentHint": ann.idempotentHint,
                "openWorldHint": ann.openWorldHint,
            },
            "input_schema": _schema_shape(t.inputSchema),
            "output_schema": _schema_shape(t.outputSchema),
        })

    resource_rows = [
        {
            "uri": str(r.uri),
            "name": r.name,
            "title": r.title,
            "description": r.description,
            "mime_type": r.mimeType,
        }
        for r in sorted(resources, key=lambda x: str(x.uri))
    ]

    return {"tools": tool_rows, "resources": resource_rows}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true",
                    help=f"write the manifest to {MANIFEST_PATH.relative_to(REPO_ROOT)}")
    args = ap.parse_args()

    manifest = asyncio.run(build_manifest())
    text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"

    if args.write:
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_text(text, encoding="utf-8")
        print(f"wrote {MANIFEST_PATH.relative_to(REPO_ROOT)} "
              f"({len(manifest['tools'])} tools, {len(manifest['resources'])} resources)")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
