"""
clustering.py — Shard community detection for NOVA.

Assigns a cluster_id to every shard by running community detection on the
knowledge graph. Used by NÓTT's SCHEDULED pass.

Algorithm priority:
  1. leidenalg + igraph  (true Leiden — install: pip install leidenalg)
  2. networkx louvain_communities  (networkx >= 2.8, available in this env)
  3. networkx connected_components  (last resort — no quality guarantee)

Recompute trigger: when the edge count in the graph has changed by more than
CLUSTER_RECOMPUTE_THRESHOLD (default 5%) since the last run.

Cluster IDs are stable string labels: "c0", "c1", ... sorted by descending
cluster size so the largest cluster is always "c0".
"""
from __future__ import annotations

import logging
from typing import Optional

from config import GRAPH_FILE

logger = logging.getLogger(__name__)

# Relation types used as edges. contradicts is excluded — it signals tension,
# not proximity, and would distort community structure.
_EDGE_RELATION_TYPES = {
    "influences", "depends_on", "extends", "references",
    "merged_from", "corroborated_by", "supersedes",
}

# Fraction of edge-count change that triggers a recompute.
import os
CLUSTER_RECOMPUTE_THRESHOLD = float(os.environ.get("NOVA_CLUSTER_RECOMPUTE_THRESHOLD", "0.05"))


# ═══════════════════════════════════════════════════════════
# GRAPH → ADJACENCY
# ═══════════════════════════════════════════════════════════

def build_adjacency(graph: dict) -> dict[str, set[str]]:
    """
    Build an undirected adjacency set from shard_graph.json.
    Only includes node IDs that appear as entities.
    """
    entities = set(graph.get("entities", {}).keys())
    adj: dict[str, set[str]] = {eid: set() for eid in entities}

    for rel in graph.get("relations", []):
        if rel.get("type") not in _EDGE_RELATION_TYPES:
            continue
        src = rel.get("source", "")
        tgt = rel.get("target", "")
        if src in entities and tgt in entities and src != tgt:
            adj[src].add(tgt)
            adj[tgt].add(src)

    return adj


# ═══════════════════════════════════════════════════════════
# RECOMPUTE TRIGGER
# ═══════════════════════════════════════════════════════════

def should_recompute(graph: dict) -> bool:
    """Return True if edge topology has changed enough to warrant re-clustering."""
    meta = graph.get("_cluster_meta", {})
    last_edge_count = meta.get("last_edge_count", -1)
    if last_edge_count < 0:
        return True  # never run

    current = len(graph.get("relations", []))
    if last_edge_count == 0:
        return current > 0
    delta = abs(current - last_edge_count) / last_edge_count
    return delta > CLUSTER_RECOMPUTE_THRESHOLD


def stamp_cluster_run(graph: dict, edge_count: int) -> None:
    """Persist last-run metadata into the graph dict (caller must save)."""
    graph.setdefault("_cluster_meta", {})["last_edge_count"] = edge_count


# ═══════════════════════════════════════════════════════════
# COMMUNITY DETECTION
# ═══════════════════════════════════════════════════════════

def _leiden(adj: dict[str, set[str]]) -> Optional[dict[str, str]]:
    """True Leiden via leidenalg + igraph. Returns None if unavailable."""
    try:
        import igraph as ig
        import leidenalg

        nodes = list(adj.keys())
        node_idx = {n: i for i, n in enumerate(nodes)}
        edges = [
            (node_idx[src], node_idx[tgt])
            for src, neighbors in adj.items()
            for tgt in neighbors
            if node_idx[src] < node_idx[tgt]
        ]
        g = ig.Graph(n=len(nodes), edges=edges, directed=False)
        partition = leidenalg.find_partition(g, leidenalg.ModularityVertexPartition)
        mapping: dict[str, str] = {}
        for cluster_idx, member_indices in enumerate(partition):
            for node_i in member_indices:
                mapping[nodes[node_i]] = str(cluster_idx)
        return mapping
    except Exception as exc:
        logger.debug("clustering._leiden unavailable: %s", exc)
        return None


def _louvain(adj: dict[str, set[str]]) -> Optional[dict[str, str]]:
    """Louvain via networkx. Returns None if unavailable."""
    try:
        import networkx as nx
        from networkx.algorithms.community import louvain_communities

        g = nx.Graph()
        g.add_nodes_from(adj.keys())
        for src, neighbors in adj.items():
            for tgt in neighbors:
                if not g.has_edge(src, tgt):
                    g.add_edge(src, tgt)

        communities = louvain_communities(g, seed=42)
        mapping: dict[str, str] = {}
        for cluster_idx, members in enumerate(communities):
            for node in members:
                mapping[node] = str(cluster_idx)
        return mapping
    except Exception as exc:
        logger.debug("clustering._louvain failed: %s", exc)
        return None


def _connected_components(adj: dict[str, set[str]]) -> dict[str, str]:
    """Fallback: label by connected component."""
    visited: dict[str, int] = {}
    cluster_idx = 0
    for node in adj:
        if node in visited:
            continue
        stack = [node]
        while stack:
            n = stack.pop()
            if n in visited:
                continue
            visited[n] = cluster_idx
            stack.extend(adj[n] - visited.keys())
        cluster_idx += 1
    return {node: str(cid) for node, cid in visited.items()}


def _stable_labels(raw: dict[str, str]) -> dict[str, str]:
    """
    Re-label clusters as "c0", "c1", ... by descending size so the largest
    cluster is always "c0" and labels are stable across runs of equal topology.
    """
    from collections import Counter
    counts = Counter(raw.values())
    rank = {old: f"c{i}" for i, (old, _) in enumerate(counts.most_common())}
    return {node: rank[cid] for node, cid in raw.items()}


def detect_communities(graph: dict) -> dict[str, str]:
    """
    Run community detection on the knowledge graph.
    Returns {shard_id: cluster_id} with stable "c0", "c1", ... labels.
    Isolated nodes (no edges) get their own singleton cluster.
    """
    adj = build_adjacency(graph)
    if not adj:
        return {}

    raw = (
        _leiden(adj)
        or _louvain(adj)
        or _connected_components(adj)
    )
    if raw is None:
        raw = _connected_components(adj)

    # Assign singleton clusters to isolated nodes not reached by any algorithm
    for node in adj:
        if node not in raw:
            raw[node] = f"singleton_{node[:8]}"

    return _stable_labels(raw)
