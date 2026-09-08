"""
Golden snapshot of NOVA's MCP wire surface.

The registry tests guard NOVA's internal metadata; this one guards what
actually leaves the process — every list a client can call: tools, resources,
resource templates and prompts. It is the check that makes titles, annotations,
schema shape, output schemas and the prompt/template inventory regression-proof.

Regenerate deliberately after an intended change:

    python utilities/dump_tool_manifest.py --write
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "utilities"))

# Rendering the manifest boots the whole server, which needs the MCP SDK and the
# local embedding stack. Skip cleanly where those are absent rather than adding
# a collection error.
pytest.importorskip("mcp.server.mcpserver", reason="MCP SDK not installed")
pytest.importorskip("sentence_transformers", reason="embedding stack not installed")

from dump_tool_manifest import MANIFEST_PATH, build_manifest  # noqa: E402


@pytest.fixture(scope="module")
def live():
    return asyncio.run(build_manifest())


@pytest.fixture(scope="module")
def golden():
    if not MANIFEST_PATH.exists():
        pytest.fail(
            f"{MANIFEST_PATH} is missing. Generate it with:\n"
            "  python utilities/dump_tool_manifest.py --write"
        )
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _by_name(manifest, key, ident):
    return {row[ident]: row for row in manifest[key]}


def test_tool_set_matches_golden(live, golden):
    live_names = sorted(t["name"] for t in live["tools"])
    golden_names = sorted(t["name"] for t in golden["tools"])
    assert live_names == golden_names, (
        f"added: {sorted(set(live_names) - set(golden_names))}  "
        f"removed: {sorted(set(golden_names) - set(live_names))}"
    )


def test_every_tool_matches_golden(live, golden):
    live_tools, golden_tools = _by_name(live, "tools", "name"), _by_name(golden, "tools", "name")
    drifted = {
        name: {"live": live_tools[name], "golden": golden_tools[name]}
        for name in sorted(set(live_tools) & set(golden_tools))
        if live_tools[name] != golden_tools[name]
    }
    assert not drifted, (
        "Published tool metadata drifted from the golden manifest. If intended:\n"
        "  python utilities/dump_tool_manifest.py --write\n"
        + json.dumps(drifted, indent=2, sort_keys=True)
    )


@pytest.mark.parametrize("section", ["resources", "resource_templates", "prompts"])
def test_section_matches_golden(live, golden, section):
    assert live[section] == golden[section], (
        f"Published {section.replace('_', ' ')} drifted. If intended:\n"
        "  python utilities/dump_tool_manifest.py --write"
    )


def test_every_tool_is_annotated_and_titled(live):
    """Independent of the golden file: these must hold for any future manifest."""
    for t in live["tools"]:
        assert t["annotations"] is not None, f"{t['name']} publishes no annotations"
        assert t["title"], f"{t['name']} publishes no title"
        assert t["summary"], f"{t['name']} publishes no description"


def test_every_tool_publishes_a_real_output_schema(live):
    """A handler annotated ``-> str`` publishes ``{"result": string}`` — one
    property and no ``$defs``. That is indistinguishable from a real union
    schema by property name alone, which is why the snapshot records defs."""
    degenerate = [
        t["name"] for t in live["tools"]
        if not (t["output_schema"] or {}).get("defs")
    ]
    assert not degenerate, (
        "These tools publish a degenerate output schema — annotate the handler "
        f"with a model from mcp/outputs.py: {degenerate}"
    )


def test_every_prompt_and_template_is_titled_and_described(live):
    for row in live["resource_templates"]:
        assert row["title"], f"{row['uri_template']} publishes no title"
        assert row["description"], f"{row['uri_template']} publishes no description"
    for row in live["prompts"]:
        assert row["title"], f"{row['name']} publishes no title"
        assert row["summary"], f"{row['name']} publishes no description"
