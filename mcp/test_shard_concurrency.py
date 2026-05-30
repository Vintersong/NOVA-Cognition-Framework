"""
test_shard_concurrency.py — regression tests for the NÓTT ↔ tool-write
lost-update race (store.mutate_shard / mutate_shard_fields).

Before the fix, every NÓTT pass and nova_shard_update did an unlocked
load → mutate → locked-write. A maintenance pass that loaded a shard, then a
concurrent nova_shard_update that appended a turn, then the pass writing back
its stale whole-object snapshot would silently drop the appended turn.

These tests pin the two guarantees that close it:
  * mutate_shard_fields applies only the changed fields onto a *fresh* read,
    so a concurrently-appended body survives.
  * mutate_shard(expect_revision=...) skips a body rewrite (compaction) when
    the shard changed under it, instead of clobbering the newer body.
"""

import store


def _seed(tmp_path, monkeypatch, sid="s", turns=("t1",), confidence=1.0):
    monkeypatch.setattr(store, "SHARD_DIR", str(tmp_path))
    fp = str(tmp_path / f"{sid}.json")
    store.save_shard(fp, {
        "shard_id": sid,
        "conversation_history": [{"t": t} for t in turns],
        "meta_tags": {"confidence": confidence},
    })
    return sid, fp


def _turns(data):
    return [t["t"] for t in data["conversation_history"]]


def test_field_merge_preserves_concurrent_body_append(tmp_path, monkeypatch):
    sid, fp = _seed(tmp_path, monkeypatch, turns=("t1",))

    # A concurrent nova_shard_update already persisted a new turn that the
    # maintenance pass never saw in its snapshot.
    data, _ = store.load_shard(sid)
    data["conversation_history"].append({"t": "t2"})
    store.save_shard(fp, data)

    # NÓTT applies a confidence decay computed from its stale snapshot
    # (it only ever knew about t1 / confidence 1.0).
    ok = store.mutate_shard_fields(
        sid, {"meta_tags": {"confidence": 1.0}}, {"meta_tags": {"confidence": 0.5}}
    )

    res, _ = store.load_shard(sid)
    assert ok is True
    assert _turns(res) == ["t1", "t2"]          # body preserved
    assert res["meta_tags"]["confidence"] == 0.5  # field still applied


def test_cas_skips_stale_body_rewrite(tmp_path, monkeypatch):
    sid, fp = _seed(tmp_path, monkeypatch, turns=("t1", "t2"))
    revision = store.shard_revision(store.load_shard(sid)[0])

    # Shard changes after the revision was captured (turn appended).
    data, _ = store.load_shard(sid)
    data["conversation_history"].append({"t": "t3"})
    store.save_shard(fp, data)

    def _replace(fresh):
        fresh.clear()
        fresh.update({
            "shard_id": sid,
            "conversation_history": [{"t": "SUMMARY"}],
            "meta_tags": {"confidence": 0.5},
        })

    written = store.mutate_shard(sid, _replace, expect_revision=revision)
    res, _ = store.load_shard(sid)
    assert written is False                       # guard tripped → skipped
    assert _turns(res) == ["t1", "t2", "t3"]      # newer body intact


def test_cas_applies_when_revision_matches(tmp_path, monkeypatch):
    sid, fp = _seed(tmp_path, monkeypatch, turns=("t1", "t2"))
    revision = store.shard_revision(store.load_shard(sid)[0])

    def _replace(fresh):
        fresh.clear()
        fresh.update({
            "shard_id": sid,
            "conversation_history": [{"t": "SUMMARY"}],
            "meta_tags": {"confidence": 0.5},
        })

    written = store.mutate_shard(sid, _replace, expect_revision=revision)
    res, _ = store.load_shard(sid)
    assert written is True
    assert _turns(res) == ["SUMMARY"]


def test_nidhogg_block_append_preserves_concurrent_turn(tmp_path, monkeypatch):
    # Nidhogg appends a provenance block; a turn appended concurrently must survive.
    import nidhogg
    sid, fp = _seed(tmp_path, monkeypatch, turns=("t1",))
    monkeypatch.setattr(nidhogg, "load_index", lambda: {})  # not used by the writer

    # Concurrent nova_shard_update persists a new turn nidhogg never snapshotted.
    data, _ = store.load_shard(sid)
    data["conversation_history"].append({"t": "t2"})
    store.save_shard(fp, data)

    nidhogg._append_nidhogg_block(
        shard_id=sid,
        source_file="/intake/doc.md",
        source_hash="abc123",
        source_type="note",
        similarity_score=0.9,
        merge_candidate=False,
        analysis={"summary": "s"},
    )

    res, _ = store.load_shard(sid)
    assert _turns(res) == ["t1", "t2"]                 # body preserved
    assert len(res["nidhogg"]) == 1                     # provenance block written
    assert res["nidhogg"][0]["source_hash"] == "abc123"


def test_nested_meta_tags_merge_is_per_subkey(tmp_path, monkeypatch):
    # NÓTT changing confidence must not wipe a concurrently-written cluster_id.
    sid, fp = _seed(tmp_path, monkeypatch, turns=("t1",), confidence=1.0)

    data, _ = store.load_shard(sid)
    data["meta_tags"]["cluster_id"] = 7  # concurrent cluster pass
    store.save_shard(fp, data)

    store.mutate_shard_fields(
        sid, {"meta_tags": {"confidence": 1.0}}, {"meta_tags": {"confidence": 0.3}}
    )

    res, _ = store.load_shard(sid)
    assert res["meta_tags"]["confidence"] == 0.3
    assert res["meta_tags"]["cluster_id"] == 7  # not clobbered
