from __future__ import annotations

import json
from pathlib import Path

import pytest

import store


def _shard(shard_id: str, *, context_summary: str = "", meta_summary: str = "") -> dict:
    meta: dict = {"confidence": 0.9}
    if meta_summary:
        meta["summary"] = meta_summary
    return {
        "shard_id": shard_id,
        "guiding_question": f"q for {shard_id}",
        "meta_tags": meta,
        "context": {"summary": context_summary, "topics": []},
    }


def test_patch_index_entry_populates_meta_summary_from_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The audit found that adversarial.py and recall.py filter on
    entry["meta"]["summary"], but enrichment only writes context.summary.
    The fix: mirror context.summary into meta.summary at index write time."""
    index_path = tmp_path / "index.json"
    monkeypatch.setattr(store, "INDEX_FILE", str(index_path))

    shard = _shard("alpha", context_summary="Distilled key facts about alpha.")
    index = store.patch_index_entry("alpha", shard)

    assert index["alpha"]["meta"]["summary"] == "Distilled key facts about alpha."
    assert index["alpha"]["context_summary"] == "Distilled key facts about alpha."


def test_patch_index_entry_does_not_overwrite_existing_meta_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If meta_tags.summary is already set (curated), don't clobber it with
    the auto-generated context summary."""
    index_path = tmp_path / "index.json"
    monkeypatch.setattr(store, "INDEX_FILE", str(index_path))

    shard = _shard(
        "beta",
        context_summary="auto-generated drivel",
        meta_summary="curated one-liner",
    )
    index = store.patch_index_entry("beta", shard)

    assert index["beta"]["meta"]["summary"] == "curated one-liner"


def test_patch_index_entry_handles_missing_context_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No context.summary → meta.summary stays unset (don't write empty key)."""
    index_path = tmp_path / "index.json"
    monkeypatch.setattr(store, "INDEX_FILE", str(index_path))

    shard = _shard("gamma", context_summary="")
    index = store.patch_index_entry("gamma", shard)

    assert "summary" not in index["gamma"]["meta"]


def test_update_index_populates_meta_summary_for_all_shards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Full rescan via update_index applies the same mirroring."""
    shard_dir = tmp_path / "shards"
    shard_dir.mkdir()
    index_path = tmp_path / "index.json"
    monkeypatch.setattr(store, "SHARD_DIR", str(shard_dir))
    monkeypatch.setattr(store, "INDEX_FILE", str(index_path))

    s1 = _shard("delta", context_summary="delta summary")
    s2 = _shard("epsilon", context_summary="")
    (shard_dir / "delta.json").write_text(json.dumps(s1), encoding="utf-8")
    (shard_dir / "epsilon.json").write_text(json.dumps(s2), encoding="utf-8")

    index = store.update_index()

    assert index["delta"]["meta"]["summary"] == "delta summary"
    assert "summary" not in index["epsilon"]["meta"]
