# Phase 4 Changes — Shard Compaction CLI + Theme Analyzer

## What was added

### `tools/shard_compact.py`

A standalone CLI script that compacts bloated NOVA shards.  **Zero external
dependencies** — uses the Python standard library only.

### `tools/theme_analyzer.py`

A standalone CLI script that clusters shards by semantic theme and auto-tags
them.  Requires `scikit-learn` (already available via `sentence-transformers`
in `mcp/requirements.txt`).

### `.github/workflows/shard-health.yml`

A CI workflow that runs `shard_compact.py --fail-on-bloat --dry-run` on every
push and pull request to `dev` and `main`.  Exits with code 1 if any shard
exceeds the 30-turn threshold, preventing bloat from silently accumulating.

---

## Why

| Problem | Solution |
|---|---|
| Token costs grow linearly as shard conversations accumulate | `shard_compact.py` trims bloated shards to a 2-turn skeleton + 1 synthetic summary, keeping context window usage flat |
| Shard themes drift as new shards are added and the store grows | `theme_analyzer.py` re-clusters the full shard store periodically, keeping themes current so Forgemaster retrieval gets smarter over time |
| Bloat can silently accumulate across merges | The CI workflow gates every merge with an automated bloat check |

---

## How to use — `shard_compact.py`

```bash
# Check for bloat without writing anything (CI / audit mode):
python tools/shard_compact.py --fail-on-bloat --dry-run

# Preview what would be compacted in the default nova_memory/ directory:
python tools/shard_compact.py --dry-run

# Compact bloated shards in a custom directory with a custom threshold:
python tools/shard_compact.py --shard-dir ./shards --threshold 20

# Process every shard regardless of turn count:
python tools/shard_compact.py --all
```

**Compaction strategy:**  when a shard's `conversation_history` exceeds
`--threshold` turns, the first 2 turns (system context / intent) are kept
verbatim and the remainder is replaced with a single synthetic turn:

```json
{
  "role": "assistant",
  "content": "[Compacted: 47 turns summarized. Key decisions preserved in shard metadata.]",
  "compacted_at": "2026-04-01T18:47:02+00:00",
  "original_turn_count": 47
}
```

The shard's top-level `last_modified` field is updated to the current ISO
timestamp after each write.

**Exit codes:**
- `0` — all shards are within threshold (or `--fail-on-bloat` is not set)
- `1` — one or more shards exceed threshold AND `--fail-on-bloat` is set

---

## How to use — `theme_analyzer.py`

```bash
# Preview cluster assignments without writing:
python tools/theme_analyzer.py --dry-run

# Cluster and tag shards in the default nova_memory/ directory:
python tools/theme_analyzer.py

# Use 5 clusters instead of the default 8:
python tools/theme_analyzer.py --n-clusters 5

# Cluster, tag, and export a nova_cluster_map.json cluster index:
python tools/theme_analyzer.py --export-cluster-map

# Combine flags — custom dir, dry-run, export map:
python tools/theme_analyzer.py --shard-dir ./shards --dry-run --export-cluster-map
```

**Clustering strategy:**
1. For each shard, the stored `context.embedding` vector (384-d from
   `all-MiniLM-L6-v2`) is used as-is.
2. If *any* shard lacks a stored embedding, TF-IDF over `guiding_question` +
   `intent` is used for **all** shards so feature dimensions match.
3. K-means (`sklearn.cluster.KMeans`) assigns each shard to one of
   `--n-clusters` clusters.
4. The theme label for each cluster is derived from the 3 most frequent
   non-stop-word tokens across all `guiding_question` fields in that cluster.
5. The derived label is written back to `shard["meta_tags"]["theme"]` for each
   shard in the cluster (unless `--dry-run`).

**Cluster map format** (written when `--export-cluster-map` is set):

```json
{
  "shard_id": "nova-cluster-map",
  "theme": "meta",
  "clusters": [
    { "id": 0, "label": "memory_context_shard", "shard_ids": ["shard-a", "shard-b"], "size": 2 },
    { "id": 1, "label": "game_design_mechanic",  "shard_ids": ["shard-c"],             "size": 1 }
  ]
}
```

---

## How Phase 4 completes the 4-phase migration

Phase 4 closes the loop on the architectural vision started in Phase 1:

```
Phase 1 — ToolPermissionContext, UsageSummary, ShardRecord, PermissionDenial
    ↓   Permission gating and usage tracking primitives
Phase 2 — NovaSession, SessionStore, nova_session_flush/load/list
    ↓   Durable, named sprint sessions with token accounting
Phase 3 — ForgemasterRuntime, nova_forgemaster_sprint
    ↓   Executable 4-turn orchestration pipeline (LLM stubs wired up)
Phase 4 — shard_compact.py, theme_analyzer.py, shard-health CI   ← you are here
    ↓   Offline maintenance layer that keeps shard health flat as the store grows
```

Without Phase 4, the NOVA shard store grows unboundedly, token costs inflate
on every context load, and Forgemaster's retrieval degrades as stale theme
labels accumulate.  Phase 4 provides the two offline maintenance scripts and
the CI guard that together enforce shard hygiene automatically.

---

## Full dependency chain

```
mcp/permissions.py          (Phase 1) ToolPermissionContext
mcp/models.py               (Phase 1) UsageSummary, ShardRecord, PermissionDenial
    ↓
mcp/session_store.py        (Phase 2) NovaSession, SessionStore
    ↓
mcp/forgemaster_runtime.py  (Phase 3) ForgemasterRuntime + nova_forgemaster_sprint tool
    ↓
tools/shard_compact.py      (Phase 4) Offline compaction CLI  ← stdlib only
tools/theme_analyzer.py     (Phase 4) Offline theme clustering CLI  ← requires sklearn
.github/workflows/
  shard-health.yml          (Phase 4) CI bloat guard
```

`shard_compact.py` and `theme_analyzer.py` are intentionally **standalone** —
they read/write shard JSON directly without importing the MCP server.  This
means they can be run as scheduled jobs, pre-commit hooks, or CI steps without
starting the server.
