from __future__ import annotations

from pathlib import Path

import pytest

import graph


def test_load_graph_returns_empty_structure_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graph, "GRAPH_FILE", str(tmp_path / "graph.json"))
    assert graph.load_graph() == {"entities": {}, "relations": []}


def test_load_graph_handles_corrupt_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    graph_file = tmp_path / "graph.json"
    graph_file.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(graph, "GRAPH_FILE", str(graph_file))
    assert graph.load_graph() == {"entities": {}, "relations": []}


def test_add_relation_deduplicates_exact_edges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graph, "GRAPH_FILE", str(tmp_path / "graph.json"))
    graph.save_graph({"entities": {}, "relations": []})

    graph.add_relation("a", "b", "references")
    graph.add_relation("a", "b", "references")

    loaded = graph.load_graph()
    assert len(loaded["relations"]) == 1


def test_query_graph_transitive_respects_depth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graph, "GRAPH_FILE", str(tmp_path / "graph.json"))
    graph.save_graph(
        {
            "entities": {},
            "relations": [
                {"source": "a", "target": "b", "type": "extends"},
                {"source": "b", "target": "c", "type": "extends"},
            ],
        }
    )

    one_hop = graph.query_graph_transitive("a", relation_type="extends", max_depth=1)
    assert [node["shard_id"] for node in one_hop] == ["b"]
