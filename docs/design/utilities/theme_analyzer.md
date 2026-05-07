# theme_analyzer.py

**One-line purpose:** Cluster all NOVA shards by semantic similarity using K-means and write derived theme labels back to each shard's `meta_tags.theme` field.

## Why it exists

Shards imported from ChatGPT or written by autoresearch carry heuristic or hardcoded theme labels. This script replaces those labels with data-derived ones by clustering all shards and naming each cluster from the most frequent content words. It is also a bulk re-tagging tool when the theme taxonomy needs to change.

## Key concepts

- **TF-IDF fallback** — When shards lack stored embeddings (`shard["context"]["embedding"]`), the script falls back to TF-IDF over `guiding_question + intent`. This is the common case.
- **K-means** — Clusters all shards into `--n-clusters` groups (default 8). The centroid-based approach means outlier shards get assigned to whichever cluster is nearest, not necessarily a meaningful one.
- **cluster label** — Derived by taking the 3 most common non-stop-words across all `guiding_question` fields in the cluster, joined with `_`. E.g., `agent_code_review`.
- **nova_cluster_map.json** — Optional export file summarising the cluster structure; written into `--shard-dir` if `--export-cluster-map` is passed.

## Public surface

- `load_shards(shard_dir) → list[tuple[Path, dict]]` — Load all `.json` files from a directory.
- `build_feature_matrix(shards) → (matrix, used_embeddings)` — Return a 2-D feature array for K-means input.
- `derive_theme_label(shards_in_cluster) → str` — Derive a human-readable label from cluster members.
- `run_analysis(shards, n_clusters, dry_run, export_cluster_map, shard_dir)` — Core analysis and write-back.
- CLI: `python tools/theme_analyzer.py [--shard-dir DIR] [--n-clusters N] [--dry-run] [--export-cluster-map]`

## Inputs and outputs

- **Reads:** `*.json` files in `--shard-dir` (default: `$NOVA_SHARD_DIR` or `shards/` at repo root).
- **Writes:** updates `meta_tags.theme` and `last_modified` in each shard JSON (unless `--dry-run`).
- **Optionally writes:** `nova_cluster_map.json` at repo root (not inside `--shard-dir`).
- **Stdout:** cluster assignments table.
- **Env:** `NOVA_SHARD_DIR` — used as the default shard directory if `--shard-dir` is not passed.

## Invariants and assumptions

- Requires `scikit-learn` (`pip install scikit-learn`). Exits with a clear error if absent.
- `n_clusters` is clamped to `len(shards)` to avoid K-means errors when the shard count is small.
- K-means with `random_state=42` produces deterministic results for the same input, but cluster-to-label mapping is not stable across runs (cluster IDs may shuffle).
- Writes float confidence values untouched — does not modify confidence.

## Callers and integration

Manual CLI only. Not imported by any other module. Mentioned in `CLAUDE.md` and `README.md` as a maintenance utility.

## Known gaps / open questions

- ~~Default `--shard-dir` was `nova_memory/` — fixed. Default now reads `$NOVA_SHARD_DIR` or falls back to `shards/` at repo root.~~ (fixed 2026-04-24)
- ~~`nova_cluster_map.json` was written into `shard_dir`, causing `shard_index.py` to pick it up as a shard — fixed. Now written to repo root.~~ (fixed 2026-04-24)
- TF-IDF is the common path (most shards have no stored embedding), so clustering quality depends on `guiding_question` text length and quality.
- K-means labels are not stable across runs — reruns can reassign shards to different theme names, overwriting prior labels.
- Theme write-back modifies shard files directly, bypassing the MCP server. `nova_shard_index` should be run afterward to sync the index.

## Changelog

| Date | Change | Why |
|---|---|---|
| 2026-04-24 | Default `--shard-dir` changed from `nova_memory/` to `$NOVA_SHARD_DIR` (fallback: `shards/`). Added `os` import. Updated docstring and argparse help text. | `nova_memory/` never existed on standard installs — plain invocations silently did nothing. |
| 2026-04-24 | `nova_cluster_map.json` output moved from `shard_dir/` to repo root. `repo_root` param added to `run_analysis`. | File written into shard dir was being picked up by `shard_index.py` as a regular shard. |
