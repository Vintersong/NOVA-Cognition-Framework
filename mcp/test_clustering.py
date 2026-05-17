"""
test_clustering.py — Unit tests for Step 9 (Leiden cluster detection).

Run: cd mcp && python test_clustering.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from clustering import (
    build_adjacency,
    detect_communities,
    should_recompute,
    stamp_cluster_run,
    _stable_labels,
    _connected_components,
)
from recall import _walk_topk_with_cluster_collapse


def _graph(*relations, entities=None) -> dict:
    """Build a minimal graph dict for testing."""
    ents = entities or {}
    if not ents:
        seen = set()
        for r in relations:
            seen.add(r["source"])
            seen.add(r["target"])
        ents = {s: {} for s in seen}
    return {"entities": ents, "relations": list(relations)}


def _rel(src, tgt, rel_type="influences") -> dict:
    return {"source": src, "target": tgt, "type": rel_type}


class TestBuildAdjacency(unittest.TestCase):

    def test_basic_edges_undirected(self):
        g = _graph(_rel("a", "b"), _rel("b", "c"))
        adj = build_adjacency(g)
        self.assertIn("b", adj["a"])
        self.assertIn("a", adj["b"])  # undirected
        self.assertIn("c", adj["b"])

    def test_contradicts_excluded(self):
        g = _graph(_rel("a", "b", "contradicts"))
        adj = build_adjacency(g)
        self.assertNotIn("b", adj.get("a", set()))

    def test_isolated_nodes_present(self):
        g = _graph(entities={"x": {}, "y": {}})
        adj = build_adjacency(g)
        self.assertIn("x", adj)
        self.assertIn("y", adj)
        self.assertEqual(adj["x"], set())

    def test_self_loops_ignored(self):
        g = _graph(_rel("a", "a"))
        adj = build_adjacency(g)
        self.assertNotIn("a", adj.get("a", set()))


class TestShouldRecompute(unittest.TestCase):

    def test_never_run_triggers(self):
        self.assertTrue(should_recompute({}))

    def test_no_change_skips(self):
        g = {"relations": [{}] * 10, "_cluster_meta": {"last_edge_count": 10}}
        self.assertFalse(should_recompute(g))

    def test_large_change_triggers(self):
        g = {"relations": [{}] * 20, "_cluster_meta": {"last_edge_count": 10}}
        self.assertTrue(should_recompute(g))

    def test_small_change_skips(self):
        g = {"relations": [{}] * 10, "_cluster_meta": {"last_edge_count": 10}}
        g["relations"].append({})  # 1/10 = 10% — above threshold
        self.assertTrue(should_recompute(g))

    def test_within_threshold_skips(self):
        # 10 → 10 (0%) — well within 5%
        g = {"relations": [{}] * 10, "_cluster_meta": {"last_edge_count": 10}}
        self.assertFalse(should_recompute(g))


class TestStampClusterRun(unittest.TestCase):

    def test_stamps_edge_count(self):
        g = {}
        stamp_cluster_run(g, 42)
        self.assertEqual(g["_cluster_meta"]["last_edge_count"], 42)

    def test_overwrites_existing(self):
        g = {"_cluster_meta": {"last_edge_count": 5}}
        stamp_cluster_run(g, 99)
        self.assertEqual(g["_cluster_meta"]["last_edge_count"], 99)


class TestDetectCommunities(unittest.TestCase):

    def test_two_clusters(self):
        # a-b-c tightly connected, d-e-f tightly connected, one bridge a-d
        g = _graph(
            _rel("a", "b"), _rel("b", "c"), _rel("a", "c"),
            _rel("d", "e"), _rel("e", "f"), _rel("d", "f"),
        )
        membership = detect_communities(g)
        self.assertEqual(set(membership.keys()), {"a", "b", "c", "d", "e", "f"})
        # a,b,c should share one cluster; d,e,f another
        abc = {membership["a"], membership["b"], membership["c"]}
        def_ = {membership["d"], membership["e"], membership["f"]}
        self.assertEqual(len(abc), 1)
        self.assertEqual(len(def_), 1)

    def test_empty_graph_returns_empty(self):
        self.assertEqual(detect_communities({}), {})

    def test_isolated_nodes_get_labels(self):
        g = _graph(entities={"x": {}, "y": {}, "z": {}})
        membership = detect_communities(g)
        self.assertIn("x", membership)
        self.assertIn("y", membership)
        self.assertIn("z", membership)

    def test_labels_start_with_c(self):
        g = _graph(_rel("a", "b"), _rel("c", "d"))
        membership = detect_communities(g)
        for label in membership.values():
            self.assertTrue(label.startswith("c") or label.startswith("singleton_"),
                            f"unexpected label: {label}")

    def test_largest_cluster_is_c0(self):
        # a-b-c-d all connected; e-f connected separately
        g = _graph(
            _rel("a", "b"), _rel("b", "c"), _rel("c", "d"),
            _rel("e", "f"),
        )
        membership = detect_communities(g)
        counts: dict[str, int] = {}
        for cid in membership.values():
            counts[cid] = counts.get(cid, 0) + 1
        largest = max(counts, key=lambda k: counts[k])
        self.assertEqual(largest, "c0")


class TestWalkTopkWithClusterCollapse(unittest.TestCase):

    def _eligible(self, shard_id, cluster_id=None) -> dict:
        meta: dict = {"summary": ""}
        if cluster_id:
            meta["cluster_id"] = cluster_id
        return {shard_id: {"meta": meta, "guiding_question": "", "confidence": 1.0}}

    def test_no_clusters_unchanged(self):
        eligible = {**self._eligible("a"), **self._eligible("b")}
        scored = [("a", 0.9), ("b", 0.8)]
        out = _walk_topk_with_cluster_collapse(scored, eligible, top_k=3)
        self.assertEqual(len(out), 2)
        self.assertNotIn("cluster_siblings", out[0])

    def test_siblings_collapsed(self):
        eligible = {**self._eligible("a", "c0"), **self._eligible("b", "c0")}
        scored = [("a", 0.9), ("b", 0.8)]
        out = _walk_topk_with_cluster_collapse(scored, eligible, top_k=3)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["shard_id"], "a")
        self.assertIn("b", out[0]["cluster_siblings"])

    def test_different_clusters_kept_separate(self):
        eligible = {**self._eligible("a", "c0"), **self._eligible("b", "c1")}
        scored = [("a", 0.9), ("b", 0.8)]
        out = _walk_topk_with_cluster_collapse(scored, eligible, top_k=3)
        self.assertEqual(len(out), 2)

    def test_walk_backfills_when_top_results_cluster(self):
        eligible = {
            **self._eligible("a", "c0"),
            **self._eligible("b", "c0"),
            **self._eligible("c", "c1"),
        }
        scored = [("a", 0.9), ("b", 0.85), ("c", 0.8)]
        out = _walk_topk_with_cluster_collapse(scored, eligible, top_k=2)
        ids = [r["shard_id"] for r in out]
        self.assertEqual(ids, ["a", "c"])
        self.assertEqual(out[0]["cluster_siblings"], ["b"])


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=2)
    passed = result.result.wasSuccessful()
    total = result.result.testsRun
    failures = len(result.result.failures) + len(result.result.errors)
    print(f"\n{'OK' if passed else 'FAILED'} — {total} tests, {failures} failures")
    sys.exit(0 if passed else 1)
