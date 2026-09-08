#!/usr/bin/env python3
"""
dump_tool_manifest.py — snapshot what an MCP client actually sees.

Renders every list a client can call — ``tools/list``, ``resources/list``,
``resources/templates/list`` and ``prompts/list`` — into a compact, stable
manifest. ``tests/test_tool_manifest.py`` diffs the live server against the
committed copy, so a change to a tool's title, annotations, schema shape or
output schema, or to the templates and prompts on offer, has to be made
deliberately rather than drifting.

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

sys.path.insert(0, str(REPO_ROOT / "utilities"))
from tool_table import load_manifest, render_tool_table, splice_table  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "mcp"))


def _summarise(description: str) -> str:
    """First sentence of a docstring, with wrapped lines rejoined.

    Taking the first *line* truncated mid-sentence on any docstring that wraps,
    which is most of them.
    """
    paragraph = (description or "").strip().split("\n\n")[0]
    flowed = " ".join(line.strip() for line in paragraph.splitlines() if line.strip())
    sentence, sep, _ = flowed.partition(". ")
    return (sentence + sep).strip() if sep else flowed


def _schema_shape(schema: dict | None) -> dict | None:
    """Top-level shape of a JSON Schema — properties, required, and model names.

    Deliberately not the whole schema; the property/required shape is what a
    model actually calls against.

    ``defs`` matters for output schemas specifically. A handler annotated
    ``-> str`` publishes ``{"result": {"type": "string"}}``, and one annotated
    with a union of models publishes ``{"result": {"anyOf": [...]}}`` — both
    have the single property ``result``, so properties alone cannot tell the
    degenerate schema from a real one. The ``$defs`` names can.
    """
    if not schema:
        return None
    return {
        "properties": sorted((schema.get("properties") or {}).keys()),
        "required": sorted(schema.get("required") or []),
        "defs": sorted((schema.get("$defs") or {}).keys()),
    }


# SDK v1 exposes camelCase attributes (``inputSchema``) and v2 exposes
# snake_case (``input_schema``), but both serialise to the same camelCase wire
# format. Reading the dumped dict rather than attributes keeps this snapshot
# working across the port — and the wire format is what we actually want to pin.
HINT_KEYS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")


def _wire(model) -> dict:
    return model.model_dump(by_alias=True, mode="json")


async def build_manifest() -> dict:
    import nova_server

    mcp = nova_server.mcp
    tools = await mcp.list_tools()
    resources = await mcp.list_resources()
    templates = await mcp.list_resource_templates()
    prompts = await mcp.list_prompts()

    tool_rows = []
    for t in sorted((_wire(x) for x in tools), key=lambda d: d["name"]):
        ann = t.get("annotations")
        tool_rows.append({
            "name": t["name"],
            "title": t.get("title"),
            "summary": _summarise(t.get("description") or ""),
            "annotations": None if ann is None else {k: ann.get(k) for k in HINT_KEYS},
            "input_schema": _schema_shape(t.get("inputSchema")),
            "output_schema": _schema_shape(t.get("outputSchema")),
        })

    resource_rows = [
        {
            "uri": str(r["uri"]),
            "name": r.get("name"),
            "title": r.get("title"),
            "description": r.get("description"),
            "mime_type": r.get("mimeType"),
        }
        for r in sorted((_wire(x) for x in resources), key=lambda d: str(d["uri"]))
    ]

    template_rows = [
        {
            "uri_template": t.get("uriTemplate"),
            "name": t.get("name"),
            "title": t.get("title"),
            "description": t.get("description"),
            "mime_type": t.get("mimeType"),
        }
        for t in sorted((_wire(x) for x in templates), key=lambda d: str(d["uriTemplate"]))
    ]

    prompt_rows = [
        {
            "name": p["name"],
            "title": p.get("title"),
            "summary": _summarise(p.get("description") or ""),
            # Argument names and requiredness are the calling contract; the
            # descriptions are prose and would make this snapshot noisy.
            "arguments": [
                {"name": a["name"], "required": bool(a.get("required"))}
                for a in sorted(p.get("arguments") or [], key=lambda a: a["name"])
            ],
        }
        for p in sorted((_wire(x) for x in prompts), key=lambda d: d["name"])
    ]

    return {
        "tools": tool_rows,
        "resources": resource_rows,
        "resource_templates": template_rows,
        "prompts": prompt_rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true",
                    help=f"write the manifest to {MANIFEST_PATH.relative_to(REPO_ROOT)}")
    ap.add_argument("--table", action="store_true",
                    help="print the markdown tool table (from the committed manifest)")
    ap.add_argument("--write-table", metavar="FILE", nargs="+",
                    help="splice the tool table into FILE(s) between the generated-table markers")
    args = ap.parse_args()

    # The table modes read the committed manifest, so they do not boot the server.
    if args.table or args.write_table:
        table = render_tool_table(load_manifest())
        if args.table:
            print(table)
        for name in args.write_table or []:
            path = REPO_ROOT / name
            changed = splice_table(path, table)
            print(f"{'updated' if changed else 'unchanged'} {path.relative_to(REPO_ROOT)}")
        return 0

    manifest = asyncio.run(build_manifest())
    text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"

    if args.write:
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_text(text, encoding="utf-8")
        print(f"wrote {MANIFEST_PATH.relative_to(REPO_ROOT)} "
              f"({len(manifest['tools'])} tools, {len(manifest['resources'])} resources, "
              f"{len(manifest['resource_templates'])} templates, "
              f"{len(manifest['prompts'])} prompts)")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
