"""
graph.py — Knowledge graph operations for NOVA.

Owns load/save/query/relate for shard_graph.json.
"""

from __future__ import annotations

import json
import os
from collections import deque
from datetime import datetime

from filelock import FileLock

from atomic_io import atomic_write_json
from config import GRAPH_FILE


# Relation types whose meaning does not depend on direction. Endpoints are
# canonicalised (sorted) before storage so A↔B is never duplicated as both
# {source: A, target: B} and {source: B, target: A}.
SYMMETRIC_RELATION_TYPES: frozenset[str] = frozenset({
    "contradicts",
    "merges_with",
    "co_occurs",
})


# ═══════════════════════════════════════════════════════════
# GRAPH I/O
# ═══════════════════════════════════════════════════════════

def load_graph() -> dict:
    if not os.path.exists(GRAPH_FILE):
        return {"entities": {}, "relations": []}
    try:
        with open(GRAPH_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"entities": {}, "relations": []}


def save_graph(graph: dict):
    with FileLock(GRAPH_FILE + ".lock", timeout=5):
        atomic_write_json(GRAPH_FILE, graph)


# ═══════════════════════════════════════════════════════════
# GRAPH MUTATIONS
# ═══════════════════════════════════════════════════════════

def add_shard_to_graph(shard_id: str, shard_data: dict):
    """Register a shard as an entity in the knowledge graph on create."""
    graph = load_graph()
    graph["entities"][shard_id] = {
        "type": "Shard",
        "guiding_question": shard_data.get("guiding_question", ""),
        "theme": shard_data.get("meta_tags", {}).get("theme", "general"),
        "intent": shard_data.get("meta_tags", {}).get("intent", "reflection"),
        "created_at": datetime.now().isoformat(),
        "confidence": shard_data.get("meta_tags", {}).get("confidence", 1.0),
    }
    save_graph(graph)


def add_relation(source_id: str, target_id: str, relation_type: str, notes: str = "", reason: str = ""):
    """Add a directed relation between two shards. Deduplicates exact matches.

    For symmetric relation types (e.g. ``contradicts``, ``merges_with``,
    ``co_occurs``) the endpoints are canonicalised by sorting so A↔B is
    stored as a single edge instead of two.
    """
    if relation_type in SYMMETRIC_RELATION_TYPES and target_id < source_id:
        source_id, target_id = target_id, source_id
    graph = load_graph()
    relation = {
        "source": source_id,
        "target": target_id,
        "type": relation_type,
        "notes": notes,
        "created_at": datetime.now().isoformat(),
    }
    if reason:
        relation["reason"] = reason
    existing = graph.get("relations", [])
    for r in existing:
        if (r["source"] == source_id
                and r["target"] == target_id
                and r["type"] == relation_type):
            return
    existing.append(relation)
    graph["relations"] = existing
    save_graph(graph)


def add_supersedes(source_id: str, target_id: str, reason: str) -> None:
    """Write a supersedes edge from source to target with a mandatory reason."""
    add_relation(source_id, target_id, "supersedes", reason=reason)


def add_corroborated_by(source_id: str, corroborating_id: str) -> None:
    """Write a corroborated_by edge: source is confirmed by corroborating_id."""
    add_relation(source_id, corroborating_id, "corroborated_by")


# ═══════════════════════════════════════════════════════════
# GRAPH QUERIES
# ═══════════════════════════════════════════════════════════

def query_graph(pattern: dict) -> list[dict]:
    """
    Simple pattern query over the knowledge graph.
    Pattern keys: source, target, type (all optional).
    Returns matching relations.
    """
    graph = load_graph()
    results = []
    for relation in graph.get("relations", []):
        match = True
        if "source" in pattern and relation["source"] != pattern["source"]:
            match = False
        if "target" in pattern and relation["target"] != pattern["target"]:
            match = False
        if "type" in pattern and relation["type"] != pattern["type"]:
            match = False
        if match:
            results.append(relation)
    return results


def query_graph_transitive(
    root_id: str,
    relation_type: str | None = None,
    direction: str = "outbound",
    max_depth: int = 3,
) -> list[dict]:
    """
    BFS traversal of the knowledge graph from root_id.
    direction: "outbound" (root is source), "inbound" (root is target), "both".
    Returns list of dicts: {shard_id, depth, path, relation_type}.
    """
    graph = load_graph()
    relations = graph.get("relations", [])
    visited: set[str] = {root_id}
    queue: deque[tuple[str, int, list[str]]] = deque([(root_id, 0, [root_id])])
    results = []

    while queue:
        current, depth, path = queue.popleft()
        if depth >= max_depth:
            continue

        for r in relations:
            if relation_type and r["type"] != relation_type:
                continue

            next_id = None
            if direction in ("outbound", "both") and r["source"] == current:
                next_id = r["target"]
            elif direction in ("inbound", "both") and r["target"] == current:
                next_id = r["source"]

            if next_id and next_id not in visited:
                visited.add(next_id)
                new_path = path + [next_id]
                results.append({
                    "shard_id": next_id,
                    "depth": depth + 1,
                    "path": new_path,
                    "relation_type": r["type"],
                })
                queue.append((next_id, depth + 1, new_path))

    return results
