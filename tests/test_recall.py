"""
test_recall.py — Unit tests for recall.py hook_recall().

Run: cd mcp && python -m pytest test_recall.py -v
"""

import time
from unittest.mock import patch


import recall
from recall import hook_recall, clear_cache


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_index(entries: list[dict]) -> dict:
    """Build a minimal shard index from a list of entry overrides."""
    index = {}
    for i, e in enumerate(entries):
        sid = e.get("shard_id", f"shard_{i:03d}")
        index[sid] = {
            "shard_id": sid,
            "guiding_question": e.get("guiding_question", "test question"),
            "tags": e.get("tags", []),
            "meta": {
                "summary": e.get("summary", "A short summary sentence."),
                "source": e.get("source", "agent_inference"),
                "theme": e.get("theme", "general"),
                "intent": e.get("intent", "reflection"),
            },
            "context_summary": e.get("context_summary", ""),
            "context_topics": e.get("context_topics", []),
            "confidence": e.get("confidence", 1.0),
            "trust_score": e.get("trust_score", 1.0),
        }
    return index


# ── Tests ─────────────────────────────────────────────────────────────────────

def setup_function():
    clear_cache()


def test_returns_empty_when_nothing_clears_floor():
    """Zero results when all shards are below min_confidence."""
    low_index = _make_index([
        {"shard_id": "s1", "confidence": 0.50, "guiding_question": "python programming"},
        {"shard_id": "s2", "confidence": 0.60, "guiding_question": "machine learning"},
    ])
    with patch("recall.load_index", return_value=low_index):
        result = hook_recall("python programming", top_k=3, min_confidence=0.85)
    assert result == [], f"Expected empty list, got {result}"


def test_returns_only_high_confidence_shards():
    """Shards below floor are excluded even if they'd rank well on relevance."""
    index = _make_index([
        {"shard_id": "high", "confidence": 0.95, "guiding_question": "python data science"},
        {"shard_id": "low",  "confidence": 0.50, "guiding_question": "python data science"},
    ])
    with patch("recall.load_index", return_value=index):
        result = hook_recall("python data science", top_k=3, min_confidence=0.85)
    ids = [r["shard_id"] for r in result]
    assert "high" in ids
    assert "low" not in ids


def test_returns_summary_only_no_body_fields():
    """Result dicts must not contain conversation body fields."""
    index = _make_index([
        {"shard_id": "s1", "confidence": 1.0, "guiding_question": "test topic",
         "summary": "This shard is about testing."},
    ])
    with patch("recall.load_index", return_value=index):
        result = hook_recall("test topic", top_k=3, min_confidence=0.85)
    assert len(result) == 1
    r = result[0]
    assert "summary" in r
    assert "conversation_history" not in r
    assert "turns" not in r
    assert "body" not in r
    assert r["summary"] == "This shard is about testing."


def test_respects_top_k_limit():
    """Never returns more than top_k results."""
    index = _make_index([
        {"shard_id": f"s{i}", "confidence": 1.0, "guiding_question": "common topic"}
        for i in range(10)
    ])
    with patch("recall.load_index", return_value=index):
        result = hook_recall("common topic", top_k=3, min_confidence=0.85)
    assert len(result) <= 3


def test_archived_shards_excluded():
    """Archived shards are excluded regardless of confidence."""
    index = _make_index([
        {"shard_id": "live",     "confidence": 1.0, "guiding_question": "active topic"},
        {"shard_id": "archived", "confidence": 1.0, "guiding_question": "active topic",
         "tags": ["archived"]},
    ])
    with patch("recall.load_index", return_value=index):
        result = hook_recall("active topic", top_k=3, min_confidence=0.85)
    ids = [r["shard_id"] for r in result]
    assert "archived" not in ids


def test_cache_returns_same_result():
    """Second call with same args returns cached result without loading index again."""
    index = _make_index([
        {"shard_id": "s1", "confidence": 1.0, "guiding_question": "cache test topic"},
    ])
    call_count = 0

    def counting_load():
        nonlocal call_count
        call_count += 1
        return index

    with patch("recall.load_index", side_effect=counting_load):
        r1 = hook_recall("cache test topic", top_k=3, min_confidence=0.85)
        r2 = hook_recall("cache test topic", top_k=3, min_confidence=0.85)

    assert call_count == 1, "load_index should be called only once for cached result"
    assert r1 == r2


def test_cache_expires_after_ttl():
    """Cache entry is evicted after TTL and index is reloaded."""
    index = _make_index([
        {"shard_id": "s1", "confidence": 1.0, "guiding_question": "expiry topic"},
    ])
    call_count = 0

    def counting_load():
        nonlocal call_count
        call_count += 1
        return index

    original_ttl = recall._CACHE_TTL
    recall._CACHE_TTL = 0.05  # 50ms for test speed
    try:
        with patch("recall.load_index", side_effect=counting_load):
            hook_recall("expiry topic", top_k=3, min_confidence=0.85)
            time.sleep(0.1)  # wait for TTL to expire
            hook_recall("expiry topic", top_k=3, min_confidence=0.85)
        assert call_count == 2, "Cache should have expired; load_index called twice"
    finally:
        recall._CACHE_TTL = original_ttl


def test_fallback_to_guiding_question_when_no_summary():
    """Falls back to guiding_question[:200] when meta_tags.summary is absent."""
    index = _make_index([
        {"shard_id": "s1", "confidence": 1.0,
         "guiding_question": "What is the meaning of life?", "summary": ""},
    ])
    with patch("recall.load_index", return_value=index):
        result = hook_recall("meaning of life", top_k=3, min_confidence=0.85)
    assert len(result) == 1
    assert result[0]["summary"] == "What is the meaning of life?"


# ── Cluster-aware top-k walk ──────────────────────────────────────────────────

def _set_cluster(index: dict, shard_id: str, cluster_id: str) -> None:
    index[shard_id]["meta"]["cluster_id"] = cluster_id


def test_cluster_walk_keeps_distinct_clusters_as_separate_results():
    """Shards in different clusters all get their own slot."""
    index = _make_index([
        {"shard_id": "a", "confidence": 1.0, "guiding_question": "alpha topic"},
        {"shard_id": "b", "confidence": 1.0, "guiding_question": "alpha topic"},
        {"shard_id": "c", "confidence": 1.0, "guiding_question": "alpha topic"},
    ])
    _set_cluster(index, "a", "c0")
    _set_cluster(index, "b", "c1")
    _set_cluster(index, "c", "c2")
    with patch("recall.load_index", return_value=index):
        result = hook_recall("alpha topic", top_k=3, min_confidence=0.85)
    assert len(result) == 3
    for r in result:
        assert r["cluster_siblings"] == []


def test_cluster_walk_collapses_same_cluster_into_siblings():
    """Multiple shards in one cluster collapse to a single representative."""
    index = _make_index([
        {"shard_id": "a", "confidence": 1.0, "guiding_question": "beta topic"},
        {"shard_id": "b", "confidence": 1.0, "guiding_question": "beta topic"},
        {"shard_id": "c", "confidence": 1.0, "guiding_question": "beta topic"},
    ])
    _set_cluster(index, "a", "c0")
    _set_cluster(index, "b", "c0")
    _set_cluster(index, "c", "c0")
    with patch("recall.load_index", return_value=index):
        result = hook_recall("beta topic", top_k=3, min_confidence=0.85)
    assert len(result) == 1
    siblings = result[0]["cluster_siblings"]
    assert sorted([result[0]["shard_id"]] + siblings) == ["a", "b", "c"]


def test_cluster_walk_backfills_beyond_top_k_when_top_results_cluster():
    """When the top scored results share a cluster, lower-ranked results in
    other clusters are pulled in to fill top_k distinct slots."""
    index = _make_index([
        {"shard_id": "a", "confidence": 1.0, "guiding_question": "gamma topic"},
        {"shard_id": "b", "confidence": 1.0, "guiding_question": "gamma topic"},
        {"shard_id": "c", "confidence": 1.0, "guiding_question": "gamma topic"},
        {"shard_id": "d", "confidence": 1.0, "guiding_question": "gamma topic"},
    ])
    _set_cluster(index, "a", "c0")
    _set_cluster(index, "b", "c0")
    _set_cluster(index, "c", "c1")
    _set_cluster(index, "d", "c2")
    fake_scored = [("a", 0.9), ("b", 0.85), ("c", 0.8), ("d", 0.75)]
    with patch("recall.load_index", return_value=index), \
         patch("recall._local_score", return_value=fake_scored):
        result = hook_recall("gamma topic", top_k=3, min_confidence=0.85)
    ids = [r["shard_id"] for r in result]
    assert ids == ["a", "c", "d"]
    assert result[0]["cluster_siblings"] == ["b"]
    assert result[1]["cluster_siblings"] == []
    assert result[2]["cluster_siblings"] == []


def test_cluster_walk_treats_shards_without_cluster_as_singletons():
    """Shards missing cluster_id are kept as-is and don't get cluster fields."""
    index = _make_index([
        {"shard_id": "a", "confidence": 1.0, "guiding_question": "delta topic"},
        {"shard_id": "b", "confidence": 1.0, "guiding_question": "delta topic"},
    ])
    # No cluster_id assigned to either.
    with patch("recall.load_index", return_value=index):
        result = hook_recall("delta topic", top_k=3, min_confidence=0.85)
    assert len(result) == 2
    for r in result:
        assert "cluster_id" not in r
        assert "cluster_siblings" not in r
