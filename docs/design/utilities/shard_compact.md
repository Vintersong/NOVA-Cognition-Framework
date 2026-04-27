# shard_compact.py

**One-line purpose:** Truncate long NOVA shard conversation histories to a 3-entry summary to keep shards from growing unboundedly.

## Why it exists

NOVA shards accumulate turns over time through `nova_shard_update`. Long conversation histories slow down shard loading and consume context window space. This script is the manual compaction path — the equivalent of `nova_shard_consolidate`'s compaction step, but runnable standalone without the MCP server. It also supports a `--fail-on-bloat` mode for CI gates.

## Key concepts

- **bloated shard** — A shard whose `conversation_history` length exceeds `--threshold` (default 30 turns).
- **compaction** — Replacing turns 3+ with a single synthetic `assistant` turn containing a human-readable note and the original turn count. Only the first 2 turns are kept verbatim.
- **dry-run** — Report what would be compacted without writing anything.

## Public surface

- `compact_history(history, n_original) → list[dict]` — Returns a 3-entry list: first 2 turns + synthetic summary turn.
- `process_shard(path, threshold, dry_run, process_all) → (name, orig_turns, compacted_turns, status)` — Process one shard file.
- `main() → int` — CLI entry point; returns 0 or 1.
- CLI: `python utilities/shard_compact.py [--shard-dir DIR] [--threshold N] [--dry-run] [--fail-on-bloat] [--all]`

## Inputs and outputs

- **Reads/writes:** `*.json` files in `--shard-dir` (default: `$NOVA_SHARD_DIR` or `shards/` at repo root).
- **Stdout:** formatted table with original and compacted turn counts per shard.
- **Exit code:** 1 if `--fail-on-bloat` is set and any shard exceeds threshold; 0 otherwise.
- **Env:** `NOVA_SHARD_DIR` — used as the default shard directory if `--shard-dir` is not passed.

## Invariants and assumptions

- Shard files must be valid JSON with a `conversation_history` list field.
- Compaction is irreversible — original turns are not archived, only the count is noted in the synthetic turn.
- Assumes the old JSON shard format; does not handle the new `.shard` format from `mcp/shard_parser.py`.
- `--all` flag compacts every shard with more than 2 turns, regardless of threshold.

## Callers and integration

Manual CLI and referenced in `tests/test_maintenance.py`. Mentioned in `CLAUDE.md` and `README.md`. Not imported by any other module. The MCP tool `nova_shard_consolidate` performs equivalent compaction server-side.

## Known gaps / open questions

- ~~Default `--shard-dir` was `nova_memory/` — fixed. Default now reads `$NOVA_SHARD_DIR` or falls back to `shards/` at repo root.~~ (fixed 2026-04-24)
- Compaction destroys content — there is no way to recover the removed turns. Should produce a backup or at minimum write the original turn count to a log.
- The synthetic compaction turn uses `"role": "assistant"` which differs from the `{user, ai}` format used in most NOVA shards. This may cause the MCP server to misparse the history after compaction.
- Float confidence in shard JSON will conflict with the new discrete `{-1, 0, 1}` model in `mcp/shard_parser.py`.

## Changelog

| Date | Change | Why |
|---|---|---|
| 2026-04-24 | Default `--shard-dir` changed from `nova_memory/` to `$NOVA_SHARD_DIR` (fallback: `shards/`). Added `os` import. Updated docstring and argparse help text. | `nova_memory/` never existed on standard installs — plain invocations silently did nothing. |
