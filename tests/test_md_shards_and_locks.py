"""
Regression tests for three storage bugs:

1. Shards migrated to .md (NÓTT converts on compaction) were invisible to the
   Arrow cache — so never decayed — and to the SQLite index rebuild.
2. utilities/shard_index.py rebuilt the index with every confidence at 1.0.
3. Graph and index writers lost each other's updates (load → edit → save
   outside the lock); the adversarial pass erased its own contradicts edges.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import graph
import shard_format
import store

REPO_ROOT = Path(__file__).resolve().parent.parent


def _shard(shard_id: str, confidence: float = 0.9, days_unused: int = 60) -> dict:
    last_used = (datetime.now(timezone.utc) - timedelta(days=days_unused)).isoformat()
    return {
        "shard_id": shard_id,
        "guiding_question": f"question {shard_id}",
        "conversation_history": [],
        "meta_tags": {"confidence": confidence, "last_used": last_used, "intent": "reflection"},
        "context": {},
    }


@pytest.fixture
def shard_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    shard_dir = tmp_path / "shards"
    shard_dir.mkdir()
    monkeypatch.setattr(store, "SHARD_DIR", str(shard_dir))
    monkeypatch.setattr(store, "INDEX_FILE", str(tmp_path / "shard_index.json"))
    (shard_dir / "as_json.json").write_text(json.dumps(_shard("as_json")), encoding="utf-8")
    (shard_dir / "as_md.json").write_text(json.dumps(_shard("as_md")), encoding="utf-8")
    shard_format.convert_shard_file(shard_dir / "as_md.json")
    store.update_index()
    return shard_dir


# ── 1. .md shards ───────────────────────────────────────────────────────────

def test_arrow_cache_decays_md_shards(shard_env: Path) -> None:
    arrow_cache = pytest.importorskip("arrow_cache")
    if not arrow_cache.ARROW_AVAILABLE:
        pytest.skip("pyarrow not installed")
    from config import DECAY_INTERVAL_DAYS, DECAY_RATE, MEMORY_KIND_DECAY_RATES

    cache = arrow_cache.ArrowShardCache(str(shard_env), store.INDEX_FILE)
    candidates = cache.decay_candidates(
        now=datetime.now(timezone.utc),
        decay_rate=DECAY_RATE,
        interval_days=DECAY_INTERVAL_DAYS,
        kind_rates=MEMORY_KIND_DECAY_RATES,
    )
    assert sorted(c[0] for c in candidates) == ["as_json", "as_md"]


def test_sqlite_rebuild_indexes_md_shards(shard_env: Path, tmp_path: Path) -> None:
    from nova_shard_db import NovaShardDB

    db = NovaShardDB(str(tmp_path / "idx.db"))
    result = db.rebuild_from_dir(str(shard_env))
    assert result == {"indexed": 2, "failed": 0}
    assert sorted(db.ids_with_prefix("as_")) == ["as_json", "as_md"]


def test_patch_index_entry_records_md_filename(shard_env: Path) -> None:
    index = store.patch_index_entry("as_md", _shard("as_md"))
    assert index["as_md"]["filename"] == "as_md.md"


# ── 2. utilities/shard_index.py ─────────────────────────────────────────────

def test_shard_index_utility_keeps_confidence(tmp_path: Path) -> None:
    (tmp_path / "shards").mkdir()
    (tmp_path / "shards" / "a.json").write_text(json.dumps(_shard("a", confidence=0.3)), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("NOVA_")}
    env["NOVA_DATA_ROOT"] = str(tmp_path)
    subprocess.run(
        [sys.executable, str(REPO_ROOT / "utilities" / "shard_index.py")],
        check=True, env=env, cwd=tmp_path, capture_output=True,
    )
    index = json.loads((tmp_path / "shard_index.json").read_text(encoding="utf-8"))
    assert index["a"]["confidence"] == 0.3


# ── 3. lost updates ─────────────────────────────────────────────────────────

def test_concurrent_add_relation_keeps_every_edge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graph, "GRAPH_FILE", str(tmp_path / "graph.json"))
    threads = [
        threading.Thread(target=graph.add_relation, args=(f"s{i}", "hub", "references"))
        for i in range(20)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(graph.load_graph()["relations"]) == 20


def test_concurrent_patch_index_entry_keeps_every_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "SHARD_DIR", str(tmp_path))
    monkeypatch.setattr(store, "INDEX_FILE", str(tmp_path / "shard_index.json"))
    threads = [
        threading.Thread(target=store.patch_index_entry, args=(f"s{i}", _shard(f"s{i}")))
        for i in range(20)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(store.load_index()) == 20


def test_adversarial_pass_persists_contradicts_edges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import adversarial

    monkeypatch.setattr(graph, "GRAPH_FILE", str(tmp_path / "graph.json"))
    monkeypatch.setattr(adversarial, "_call_gemini", lambda prompt: "stub")
    monkeypatch.setattr(
        adversarial, "_parse_contradictions",
        lambda response, ids: [{"source": "a", "target": "b", "reason": "r"}],
    )
    index = {s: {"shard_id": s, "confidence": 1.0, "tags": [], "meta": {"summary": "x"}} for s in "ab"}

    out = adversarial.run_adversarial_pass(index, graph.load_graph(), graph.update_graph, graph.add_relation)

    saved = graph.load_graph()
    assert out["contradictions_found"] == 1
    assert [r["type"] for r in saved["relations"]] == ["contradicts"]
    assert saved["_adversarial_meta"]["last_pass_id"] == out["pass_id"]
