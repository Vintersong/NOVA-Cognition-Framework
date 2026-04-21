from __future__ import annotations

import json
from pathlib import Path

import pytest

import store


def test_load_index_returns_empty_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "INDEX_FILE", str(tmp_path / "missing-index.json"))
    assert store.load_index() == {}


def test_load_index_handles_corrupt_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    index_path = tmp_path / "index.json"
    index_path.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(store, "INDEX_FILE", str(index_path))

    assert store.load_index() == {}


def test_load_shard_rejects_path_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "SHARD_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="outside shard directory"):
        store.load_shard("../escape")


def test_update_index_skips_invalid_shards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shard_dir = tmp_path / "shards"
    shard_dir.mkdir()
    (shard_dir / "bad.json").write_text("{not-json", encoding="utf-8")
    (shard_dir / "good.json").write_text(
        json.dumps(
            {
                "shard_id": "good",
                "guiding_question": "ok",
                "meta_tags": {"confidence": 0.9},
                "conversation_history": [],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(store, "SHARD_DIR", str(shard_dir))
    monkeypatch.setattr(store, "INDEX_FILE", str(tmp_path / "shard_index.json"))

    index = store.update_index()
    assert "good" in index
    assert "bad" not in index
