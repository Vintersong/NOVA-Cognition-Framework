# build_summary_index.py

**One-line purpose:** CLI entry point to batch-build `summary_index.json` and `summary_index.md` from existing shards.

## Why it exists

`summary_index.json` stores one-line summaries and longer synopses for each shard — used by `nova_shard_index` to build the browse view. When shards are added in bulk (migration, import) without going through `nova_shard_create`, the summary index is stale. This script fills the gap by calling `store.rebuild_summary_indexes` directly.

## Key concepts

- **Resumable** — `load_summary_index()` is called first; existing entries are preserved. Only shards missing a `d` (description) field are sent to Haiku for batch generation.
- **Batch size 5** — `generate_missing=True, batch_size=5` sends at most 5 shards per Haiku call to stay within token limits.

## Public surface

- `main()` — load existing index, rebuild missing summaries, print JSON status to stdout.

## Inputs and outputs

- **Reads:** `shard_index.json`, `summary_index.json`, `SHARD_DIR/*.json`.
- **Writes:** `summary_index.json`, `summary_index.md` (via `store.rebuild_summary_indexes`).
- **Env:** inherits from `store.py` (`NOVA_SHARD_DIR`, `NOVA_SUMMARY_INDEX_FILE`, etc.) and `CLAUDE_API_KEY` for Haiku calls.

## Invariants and assumptions

- Must be run from `mcp/` directory (`cd mcp && python build_summary_index.py`) — relies on `store.py` being importable from the working directory.
- If `CLAUDE_API_KEY` is absent, `generate_haiku_summary_batch` in `store.py` skips the Haiku call and falls back to deriving summaries from `guiding_question` — this is intentional and functional, not a failure.

## Callers and integration

- Not imported by any other module — standalone CLI only.
- Mirrors the functionality available via `nova_shard_summary` MCP tool but callable outside the server.

## Known gaps / open questions

- No `argparse` — batch size and shard directory are not configurable at the CLI level without editing the source.
- Output goes to stdout as JSON; no logging to file. For large shard stores this may produce a very large JSON blob.
