# test_nova.py

**One-line purpose:** Interactive NOVA memory explorer — reads the live shard store and prints theme distribution, confidence health, spotlight shards, cross-theme search results, and decay watch.

## Why it exists

During development and maintenance, it is useful to get a quick ASCII snapshot of what NOVA knows: which themes dominate, which shards are low-confidence, what the top shard per theme looks like, and which shards are oldest and most likely to decay. This script provides that view without needing to construct MCP tool calls manually.

## Key concepts

- **Direct import of `nova_server`** — calls `nova_shard_index`, `nova_shard_search`, `nova_shard_get` as async functions via `asyncio.run(explore())`. This is the same code path as the live server.
- **Browse row fields** — uses the compact browse row format from `nova_shard_index`: `id`, `t` (tags), `c` (confidence float), `created`, `n` (turn count). Handles both `conversation_history` and `turns` (autoresearch) shard formats in `excerpt()`.
- **`confidence_band`** — float-based thresholds: `>= 0.75` = high, `>= 0.40` = at-risk, `< 0.40` = low. These bands are float-only and will need revisiting after the discrete confidence migration.
- **Cross-theme search** — hardcoded query list: `["AI agents", "game design", "warfare", "creativity", "future"]`. Not configurable without editing the source.

## Public surface

- `explore()` (async) — main display function; runs all 6 display sections.

## Inputs and outputs

- **Reads:** `SHARD_DIR/*.json` (via MCP tool functions), `shard_index.json`.
- **Writes:** stdout only.
- **Env:** inherits from `nova_server.py` (loads `.env` at import time via `nova_server`).

## Invariants and assumptions

- **[USER NARRATION NEEDED]** `confidence_band` thresholds (0.75, 0.40) are float-based — incompatible with discrete `{-1, 0, 1}` confidence. After migration, this display will be meaningless or break.
- Requires `nova_server.py` to be importable (i.e. run from `mcp/` directory or with `mcp/` on `PYTHONPATH`).
- `stdout.reconfigure(encoding="utf-8")` is called at the top — handles Windows terminals that default to cp1252.
- `per_page=200` in `ShardIndexInput` — fetches at most 200 shards; stores with more will be silently truncated.

## Callers and integration

- Not imported by any other module. CLI-only: `cd mcp && python test_nova.py`.

## Known gaps / open questions

- Hardcoded cross-theme query list — not useful if none of the queries match anything in a given shard store.
- `per_page=200` cap — large shard stores will silently miss entries beyond the first 200.
- Float confidence display bands become meaningless after the discrete confidence migration.
- The script name (`test_nova.py`) is misleading — it is an interactive explorer, not a pytest test suite. It runs no assertions and produces no pass/fail output.
