# shard_index.py

**One-line purpose:** Scan the shards directory and build/rebuild `shard_index.json`, a flat dict index of all shards with metadata and status tags.

## Why it exists

The MCP server and other utilities need a fast way to discover all shards without loading every JSON file on every query. `shard_index.json` is that cache. This script is the standalone rebuild tool for when the index falls out of sync with the shard directory (e.g., after manual deletions, migrations, or `dedup_json.py` runs).

## Key concepts

- **shard index** — A dict keyed by `shard_id`, each value containing `filename`, `guiding_question`, `tags`, `meta`, `context_summary`, `context_topics`, and `confidence`. Written to `shard_index.json`.
- **status tags** — Auto-derived labels: `recent` (accessed within 3 days), `stale` (not accessed in 14 days), `frequently_used` (usage_count > 10), `archived`, `enriched` (has stored embedding).
- **legacy migration** — If the index file is in the old list-based format (with a `"shards"` key), `load_index()` silently rebuilds it in the new dict format.

## Public surface

- `build_index() → dict` — Scan `SHARD_DIR`, return the full index dict (does not write to disk).
- `update_index() → dict` — Rebuild and save the index to `INDEX_FILE`. Prints confirmation.
- `load_index() → dict` — Load the existing index from disk; returns `{}` if missing.
- `save_index(index)` — Write the index dict to `INDEX_FILE`.
- `classify_tags(shard) → list[str]` — Derive status tags for a single shard.
- `load_shard_file(filepath) → dict | None` — Load and parse one shard JSON.
- CLI (run as script): calls `update_index()` and exits.
- **Env:** `NOVA_SHARD_DIR` (default: `shards/`), `NOVA_INDEX_FILE` (default: `shard_index.json`), `NOVA_RECENT_DAYS` (default: 3), `NOVA_STALE_DAYS` (default: 14).

## Inputs and outputs

- **Reads:** all `*.json` files in `SHARD_DIR`.
- **Writes:** `INDEX_FILE` (`shard_index.json` at repo root by default).
- **Stdout:** one confirmation line per `update_index()` call.

## Invariants and assumptions

- `SHARD_DIR` must contain flat `*.json` shard files — no subdirectory recursion.
- `shard_id` is read from the `shard_id` field in the JSON; falls back to filename stem if absent.
- `confidence` is read as a float from the shard's top-level `confidence` field — assumes old float model.
- `classify_tags` reads `meta_tags.last_used` as an ISO-format datetime string.

## Callers and integration

- Imported by `utilities/dedup_json.py` (for `SHARD_DIR` and `update_index`).
- Referenced in `mcp/nova_server.py`, `mcp/evolve.py`, `mcp/config.py`, `mcp/wiki.py` — primarily for `SHARD_DIR` path resolution.
- Mentioned in `CLAUDE.md`, `README.md`, `docs/ROADMAP.md`, and several other docs.
- Run as CLI manually after bulk shard operations.

## Known gaps / open questions

- Confidence is stored as a float; the new `mcp/shard_parser.py` model uses discrete `{-1, 0, 1}`. After migration, `build_index()` will write stale float values.
- `classify_tags` checks `shard.get("context", {}).get("embedding")` for the `enriched` tag, but most shard writers do not populate `context.embedding`. The tag may never appear in practice.
- The `context_summary` and `context_topics` fields in the index entry rely on `shard.get("context", {})` — if the context extractor was never run, these are always empty.
