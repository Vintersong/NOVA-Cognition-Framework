# graph.py

**One-line purpose:** Load, save, query, and mutate `shard_graph.json` — the inter-shard knowledge graph of entities and directed relations.

## Why it exists

Shards are isolated JSON files; `graph.py` provides the connective tissue — a persistent record of how shards relate to each other (`depends_on`, `influences`, `contradicts`, etc.) so the orchestrator can traverse dependencies before dispatching tickets.

## Key concepts

- **entity** — a shard registered in the graph under its `shard_id`. Stores `type`, `guiding_question`, `theme`, `intent`, `created_at`, and `confidence`.
- **relation** — a directed edge `{source, target, type, notes, created_at}`. Types include `influences`, `depends_on`, `contradicts`, `extends`, `references`.
- **transitive query** — BFS from a root shard along relations; useful for finding all shards a given shard depends on before starting work.

## Public surface

- `load_graph() → dict` — load `shard_graph.json`; returns `{"entities": {}, "relations": []}` on any failure.
- `save_graph(graph)` — file-locked write.
- `add_shard_to_graph(shard_id, shard_data)` — register a shard as an entity on create.
- `add_relation(source_id, target_id, relation_type, notes)` — add a directed relation; deduplicates exact `(source, target, type)` matches.
- `query_graph(pattern) → list[dict]` — filter relations by `source`, `target`, and/or `type` (all optional).
- `query_graph_transitive(root_id, relation_type, direction, max_depth) → list[dict]` — BFS traversal returning `{shard_id, depth, path, relation_type}` for each reachable node.

## Inputs and outputs

- **Reads/writes:** `GRAPH_FILE` (`shard_graph.json` at repo root by default).
- **Env:** `NOVA_GRAPH_FILE`.

## Invariants and assumptions

- `shard_graph.json` has top-level keys `entities` (dict) and `relations` (list).
- `add_shard_to_graph` stores float `confidence` from `meta_tags.confidence` — assumes old float model.
- Relation deduplication checks `source + target + type`; `notes` differences do not create a new relation.
- BFS `max_depth` default is 3; `direction` can be `outbound`, `inbound`, or `both`.

## Callers and integration

- `nova_server.py` — imports all graph functions for `nova_graph_query` and `nova_graph_relate` tools.
- `nott.py` — `_graph_sync` updates entity confidence values after a maintenance cycle.
- No external callers outside `mcp/`.

## Known gaps / open questions

- `add_shard_to_graph` stores float confidence in the entity — this will be stale after the migration to discrete `{-1, 0, 1}`.
- `nott._graph_sync` syncs entity confidence from the index (also float) — same issue.
- The graph has no removal mechanism for entities or relations: deleted or forgotten shards leave orphan nodes.
- No index on `relations` list — graph queries are O(n) linear scans over all relations. Fine at current scale; will degrade with hundreds of shards and thousands of relations.
