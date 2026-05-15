"""
spreading_activation.py — Graph-based relevance propagation for MUNINN.

Third-pass retrieval: propagates scores from high-confidence seed shards
through the knowledge graph using damped BFS with cluster-aware boundary
penalties.

Algorithm:
  - Build directed outgoing adjacency from graph["relations"], excluding
    contradicts edges (which signal tension, not proximity)
  - BFS from each seed shard (score >= confidence_floor), up to max_hops hops
  - Per edge: propagated = node_score * edge_weight * damping * cluster_factor
  - cluster_factor = 1.0 (same cluster) or 0.25 (cross-cluster boundary)
  - Accumulate: activation[neighbor] += propagated
  - Seed shard IDs are excluded from the returned dict (they keep their
    direct MUNINN scores)

Called lazily from Muninn.rerank() to avoid circular imports.
"""

from __future__ import annotations

import logging
from collections import deque

logger = logging.getLogger(__name__)

# contradicts signals tension between shards — excluded from propagation
_EXCLUDED_EDGE_TYPES: frozenset[str] = frozenset({"contradicts"})

# Semantic strength per relation type (graph schema stores no numeric weights)
_EDGE_WEIGHTS: dict[str, float] = {
    "influences":      1.00,
    "corroborated_by": 0.90,
    "depends_on":      0.85,
    "extends":         0.80,
    "merged_from":     0.70,
    "references":      0.60,
    "supersedes":      0.50,
}
_DEFAULT_EDGE_WEIGHT = 0.50

_INTRA_CLUSTER_FACTOR = 1.0
# Inter-cluster edges get 0.25× the base damping — boundary penalty
_INTER_CLUSTER_FACTOR = 0.25


def spreading_activation(
    seeds: dict[str, float],
    graph: dict,
    cluster_map: dict[str, str],
    damping: float = 0.5,
    max_hops: int = 2,
    confidence_floor: float = 0.0,
) -> dict[str, float]:
    """
    Propagate relevance scores from seed shards through the knowledge graph.

    Args:
        seeds:            shard_id → MUNINN relevance score. Seeds with score
                          below confidence_floor are skipped.
        graph:            loaded graph {"entities": {...}, "relations": [...]}
        cluster_map:      shard_id → cluster_id from detect_communities().
                          May be empty or partial; shards absent from the map
                          are treated as cross-cluster (conservative).
        damping:          decay multiplier applied at each hop (default 0.5).
        max_hops:         maximum BFS depth from any seed (default 2).
        confidence_floor: minimum seed score to start propagation from that
                          seed (default 0.0 — all seeds propagate).

    Returns:
        Activation scores for non-seed shards: {shard_id: float}.
        Seed shard IDs are excluded from the result.
    """
    entities: dict = graph.get("entities", {})

    # Build directed outgoing adjacency, excluding contradicts edges
    # adj[src] = [(target_id, edge_type), ...]
    adj: dict[str, list[tuple[str, str]]] = {}
    for rel in graph.get("relations", []):
        rel_type = rel.get("type", "")
        if rel_type in _EXCLUDED_EDGE_TYPES:
            continue
        src = rel.get("source", "")
        tgt = rel.get("target", "")
        if not src or not tgt or src == tgt:
            continue
        adj.setdefault(src, []).append((tgt, rel_type))

    seed_ids: frozenset[str] = frozenset(seeds)
    activation: dict[str, float] = {}

    for seed_id, seed_score in seeds.items():
        if seed_score < confidence_floor:
            continue

        seed_cluster = cluster_map.get(seed_id)

        # BFS: (current_node, score_at_node, current_depth)
        # visited is per-seed: prevents cycles within one seed's walk while
        # allowing a neighbor to accumulate activation from multiple seeds.
        visited: set[str] = {seed_id}
        queue: deque[tuple[str, float, int]] = deque([(seed_id, seed_score, 0)])

        while queue:
            node_id, node_score, depth = queue.popleft()
            if depth >= max_hops:
                continue

            for neighbor_id, rel_type in adj.get(node_id, []):
                if neighbor_id not in entities:
                    continue

                edge_weight = _EDGE_WEIGHTS.get(rel_type, _DEFAULT_EDGE_WEIGHT)

                neighbor_cluster = cluster_map.get(neighbor_id)
                # Use seed's cluster as the origin cluster for the whole wave.
                # None on either side → treat as cross-cluster (conservative).
                if (
                    seed_cluster is not None
                    and neighbor_cluster is not None
                    and seed_cluster == neighbor_cluster
                ):
                    cluster_factor = _INTRA_CLUSTER_FACTOR
                else:
                    cluster_factor = _INTER_CLUSTER_FACTOR

                propagated = node_score * edge_weight * damping * cluster_factor
                if propagated <= 0.0:
                    continue

                if neighbor_id not in seed_ids:
                    activation[neighbor_id] = (
                        activation.get(neighbor_id, 0.0) + propagated
                    )

                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    queue.append((neighbor_id, propagated, depth + 1))

    return activation
