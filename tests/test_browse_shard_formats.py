"""
test_browse_shard_formats.py — the browse projection sees both shard formats.

``store.iter_shard_skeletons`` globbed ``*.json`` while ``load_shard_file``
prefers ``.md``, so a shard migrated to Markdown was readable by id and
invisible to every browse tool. Nothing caught it because no test ever put a
``.md`` shard on disk.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import store
from shard_format import shard_to_md


def _shard(shard_id: str, question: str, turns: int = 2) -> dict:
    return {
        "shard_id": shard_id,
        "guiding_question": question,
        "tags": ["testing"],
        "conversation_history": [
            {"timestamp": f"2026-01-0{i + 1}T00:00:00", "user": f"u{i}", "ai": f"a{i}"}
            for i in range(turns)
        ],
        "meta_tags": {
            "theme": "testing",
            "intent": "reflection",
            "confidence": 0.75,
            "last_used": "2026-01-02T00:00:00",
            "usage_count": 1,
        },
        "context": {"summary": f"Summary of {shard_id}.", "topics": ["t"]},
    }


@pytest.fixture()
def shard_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(store, "SHARD_DIR", str(tmp_path))
    return tmp_path


def test_both_formats_appear_in_the_browse_projection(shard_dir):
    import json

    (shard_dir / "json_shard.json").write_text(
        json.dumps(_shard("json_shard", "Stored as JSON?")), encoding="utf-8",
    )
    (shard_dir / "md_shard.md").write_text(
        shard_to_md(_shard("md_shard", "Stored as Markdown?")), encoding="utf-8",
    )

    rows = {r["id"]: r for r in store.iter_shard_skeletons()}
    assert set(rows) == {"json_shard", "md_shard"}

    md = rows["md_shard"]
    assert md["guiding_question"] == "Stored as Markdown?"
    assert md["theme"] == "testing"
    assert md["confidence"] == 0.75
    assert md["turn_count"] == 2
    assert md["synopsis_source"] == "Summary of md_shard."
    assert "testing" in md["tags"]


def test_the_two_readers_agree_on_the_same_shard(shard_dir):
    """The same shard in either format must browse identically, or migrating a
    shard would silently change how it lists."""
    import json

    data = _shard("twin", "Does the format matter?", turns=3)
    (shard_dir / "twin.json").write_text(json.dumps(data), encoding="utf-8")
    json_row = store.read_shard_skeleton(shard_dir / "twin.json")

    (shard_dir / "twin.json").unlink()
    (shard_dir / "twin.md").write_text(shard_to_md(data), encoding="utf-8")
    md_row = store.read_md_shard_skeleton(shard_dir / "twin.md")

    assert md_row == json_row


def test_in_flight_atomic_writes_are_skipped(shard_dir):
    """atomic_io writes a ".tmp_*" sibling and renames it into place. Globbing
    it and then reading it is a guaranteed race."""
    import json

    (shard_dir / "real.json").write_text(json.dumps(_shard("real", "Q?")), encoding="utf-8")
    (shard_dir / ".tmp_abc123.json").write_text("{ partial", encoding="utf-8")

    assert [r["id"] for r in store.iter_shard_skeletons()] == ["real"]


def test_unknown_extensions_are_ignored(shard_dir):
    (shard_dir / "notes.txt").write_text("not a shard", encoding="utf-8")
    (shard_dir / "index.lock").write_text("", encoding="utf-8")

    assert store.iter_shard_skeletons() == []
