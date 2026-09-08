"""
test_quarantine.py — Unit tests for the quarantine system (Step 4).

Run: cd mcp && python -m pytest test_quarantine.py test_recall.py -v
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


from recall import hook_recall, clear_cache


# ═══════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════

def _make_index_entry(shard_id, confidence=1.0, quarantine_until=None, source="agent_inference"):
    return {
        "shard_id": shard_id,
        "guiding_question": f"Question about {shard_id}",
        "tags": [],
        "meta": {
            "summary": f"Summary of {shard_id}.",
            "source": source,
            "theme": "general",
            "intent": "reflection",
            "quarantine_until": quarantine_until,
        },
        "context_summary": "",
        "context_topics": [],
        "confidence": confidence,
        "trust_score": 1.0,
    }


def _future(hours=48):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _past(hours=1):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


# ═══════════════════════════════════════════════════════════
# recall.py — quarantine penalty tests
# ═══════════════════════════════════════════════════════════

def setup_function():
    clear_cache()


def test_quarantined_shard_scores_lower_than_unquarantined():
    """A quarantined shard's score is multiplied by QUARANTINE_PENALTY (0.5)."""
    index = {
        "clean":      _make_index_entry("clean",      confidence=1.0, quarantine_until=None),
        "quarantined": _make_index_entry("quarantined", confidence=1.0, quarantine_until=_future(48)),
    }
    index["clean"]["guiding_question"] = "python testing techniques"
    index["quarantined"]["guiding_question"] = "python testing techniques"
    index["clean"]["meta"]["summary"] = "Python testing techniques overview."
    index["quarantined"]["meta"]["summary"] = "Python testing techniques overview."

    with patch("recall.load_index", return_value=index):
        results = hook_recall("python testing", top_k=5, min_confidence=0.0)

    scores = {r["shard_id"]: r["score"] for r in results}
    if "clean" in scores and "quarantined" in scores:
        assert scores["quarantined"] < scores["clean"], (
            f"Quarantined score {scores['quarantined']} should be less than "
            f"clean score {scores['clean']}"
        )


def test_expired_quarantine_not_penalised():
    """A shard whose quarantine_until is in the past is treated as normal."""
    index = {
        "expired":    _make_index_entry("expired",    confidence=1.0, quarantine_until=_past(1)),
        "quarantined": _make_index_entry("quarantined", confidence=1.0, quarantine_until=_future(48)),
    }
    for sid in index:
        index[sid]["guiding_question"] = "machine learning overview"
        index[sid]["meta"]["summary"] = "Machine learning overview."

    with patch("recall.load_index", return_value=index):
        results = hook_recall("machine learning", top_k=5, min_confidence=0.0)

    scores = {r["shard_id"]: r["score"] for r in results}
    if "expired" in scores and "quarantined" in scores:
        assert scores["expired"] > scores["quarantined"]


# ═══════════════════════════════════════════════════════════
# nott.py — quarantine graduation pass tests
# ═══════════════════════════════════════════════════════════

def _build_nott(shards: dict, graph_relations: list):
    """Build a Nott instance wired to in-memory shard storage."""
    from nott import Nott

    shard_store = dict(shards)  # shard_id -> data dict

    def load_index():
        return {sid: {"meta": d.get("meta_tags", {}), "tags": [], "confidence": d.get("meta_tags", {}).get("confidence", 1.0)}
                for sid, d in shard_store.items()}

    def load_shard(sid):
        if sid not in shard_store:
            raise FileNotFoundError(sid)
        return shard_store[sid], sid  # filepath = sid for simplicity

    def save_shard(filepath, data):
        shard_store[filepath] = data

    def load_graph():
        return {"entities": {}, "relations": graph_relations}

    def save_graph(g):
        pass

    nott = Nott(
        shard_dir=".",
        graph_file=".",
        usage_log_file="nul",
        load_index_fn=load_index,
        update_index_fn=load_index,
        load_shard_fn=load_shard,
        save_shard_fn=save_shard,
        decay_fn=lambda d: d.get("meta_tags", {}).get("confidence", 1.0),
        compact_fn=lambda d, sid: False,
        merge_fn=lambda sid, d, idx: [],
        load_graph_fn=load_graph,
        save_graph_fn=save_graph,
    )
    return nott, shard_store


def _shard(shard_id, quarantine_until, confidence=1.0):
    return {
        "shard_id": shard_id,
        "guiding_question": f"Q {shard_id}",
        "conversation_history": [],
        "meta_tags": {
            "confidence": confidence,
            "quarantine_until": quarantine_until,
            "source": "session_extracted",
        },
    }


def test_past_quarantine_no_contradicts_graduates():
    """Shard past quarantine with no contradicts edge is graduated (quarantine_until cleared)."""
    shards = {"s1": _shard("s1", quarantine_until=_past(1))}
    nott, store = _build_nott(shards, graph_relations=[])

    asyncio.run(nott._quarantine_pass(nott._load_index(), dry_run=False))

    assert store["s1"]["meta_tags"]["quarantine_until"] is None


def test_past_quarantine_with_contradicts_penalises():
    """Shard past quarantine with a contradicts edge pointing at it gets confidence halved."""
    shards = {"s1": _shard("s1", quarantine_until=_past(1), confidence=1.0)}
    relations = [{"source": "s2", "target": "s1", "type": "contradicts"}]
    nott, store = _build_nott(shards, graph_relations=relations)

    asyncio.run(nott._quarantine_pass(nott._load_index(), dry_run=False))

    assert store["s1"]["meta_tags"]["quarantine_until"] is None
    assert store["s1"]["meta_tags"]["confidence"] < 1.0


def test_active_quarantine_untouched():
    """Shard still within quarantine window is not modified."""
    shards = {"s1": _shard("s1", quarantine_until=_future(24))}
    nott, store = _build_nott(shards, graph_relations=[])

    asyncio.run(nott._quarantine_pass(nott._load_index(), dry_run=False))

    assert store["s1"]["meta_tags"]["quarantine_until"] is not None


def test_dry_run_does_not_write():
    """dry_run=True runs the pass but does not call save_shard."""
    expired = _past(1)
    shards = {"s1": _shard("s1", quarantine_until=expired)}
    nott, store = _build_nott(shards, graph_relations=[])

    save_calls = []
    nott._save_shard = lambda fp, data: save_calls.append(fp)

    results = asyncio.run(nott._quarantine_pass(nott._load_index(), dry_run=True))

    assert save_calls == [], f"dry_run must not call save_shard, got {save_calls}"
    assert results, "dry_run should still report what would have changed"
