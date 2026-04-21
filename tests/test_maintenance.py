from __future__ import annotations

from pathlib import Path

import pytest

import maintenance


def test_apply_confidence_decay_invalid_last_used_no_change() -> None:
    shard = {"meta_tags": {"confidence": 0.75, "last_used": "not-a-date"}}
    assert maintenance.apply_confidence_decay(shard) == 0.75
    assert shard["meta_tags"]["confidence"] == 0.75


def test_maybe_compact_shard_compacts_when_threshold_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(maintenance, "COMPACT_THRESHOLD", 3)
    monkeypatch.setattr(maintenance, "COMPACT_KEEP_RECENT", 1)
    monkeypatch.setattr(maintenance, "_generate_compaction_summary", lambda turns, sid: f"summary:{sid}:{len(turns)}")

    shard = {
        "conversation_history": [{"user": "u1"}, {"user": "u2"}, {"user": "u3"}],
        "context": {},
        "meta_tags": {},
    }

    compacted = maintenance.maybe_compact_shard(shard, "s1")
    assert compacted is True
    assert len(shard["conversation_history"]) == 1
    assert "COMPACTED" in shard["context"]["summary"]


def test_find_merge_candidates_skips_missing_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(maintenance, "SHARD_DIR", str(tmp_path))
    monkeypatch.setattr(maintenance, "MERGE_SIMILARITY_THRESHOLD", 0.5)

    current = {"context": {"embedding": [1.0, 0.0]}}
    index = {"missing": {"tags": []}}
    assert maintenance.find_merge_candidates("current", current, index) == []


def test_cosine_similarity_handles_mismatched_lengths() -> None:
    assert maintenance.cosine_similarity([1.0], [1.0, 2.0]) == 0.0
