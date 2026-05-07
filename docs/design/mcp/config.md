# config.py

**One-line purpose:** Single source of truth for all environment variable defaults — every other module imports constants from here instead of reading `os.environ` directly.

## Why it exists

Prevents env var name inconsistency and scattered defaults across modules. If a default needs to change, one line here propagates everywhere.

## Key concepts

- **NOTT_COUNT_THRESHOLD** — shard count that triggers NÓTT's COUNT_THRESHOLD maintenance cycle.
- **HUGINN_CONFIDENCE_THRESHOLD** — HUGINN score above which MUNINN is skipped.
- **CONFIDENCE_LOW_THRESHOLD** — shards below this float value receive the `low_confidence` tag.

## Public surface

All values are module-level constants — no classes, no functions except `parse_bool_env`.

- `parse_bool_env(key, default) → bool` — parses common truthy strings (`"1"`, `"true"`, `"yes"`, `"on"`).
- Constants: `SHARD_DIR`, `INDEX_FILE`, `GRAPH_FILE`, `SUMMARY_INDEX_FILE`, `SUMMARY_MARKDOWN_FILE`, `USAGE_LOG_FILE`, `SESSION_STORE_DIR`, `WIKI_DIR`, `WIKI_SCHEMA_FILE`, `WIKI_INDEX_FILE`, `MAX_FRAGMENTS`, `COMPACT_THRESHOLD`, `COMPACT_KEEP_RECENT`, `DECAY_RATE`, `DECAY_INTERVAL_DAYS`, `MERGE_SIMILARITY_THRESHOLD`, `CONFIDENCE_LOW_THRESHOLD`, `RECENT_ACCESS_DAYS`, `STALE_ACCESS_DAYS`, `HUGINN_CONFIDENCE_THRESHOLD`, `NOTT_COUNT_THRESHOLD`, `CLAUDE_API_KEY`, `HUGINN_MODEL`, `MUNINN_MODEL`, `GEMINI_MODEL`, `SESSION_ID_PATTERN`, `WIKI_ROUTING_MODEL`, `WIKI_SYNTHESIS_MODEL`.

## Inputs and outputs

- **Reads:** environment variables at import time.
- **Writes:** nothing.
- **Env vars:** all of the above (see `CLAUDE.md` for full table).

## Invariants and assumptions

- All path constants are resolved relative to `_REPO_ROOT` (two levels up from `mcp/`) if no env var is set.
- `DECAY_RATE` and `CONFIDENCE_LOW_THRESHOLD` are floats — inherited from the old confidence model.
- Read once at import time; server restart required to pick up `.env` changes (except `forgemaster_runtime.py`, which re-reads `.env` on each dispatch call).

## Callers and integration

Imported by every module in `mcp/`. The single most-referenced file in the codebase.

## Known gaps / open questions

- `CONFIDENCE_LOW_THRESHOLD = 0.4` (float) and `DECAY_RATE = 0.05` (float) are holdovers from the old model. After migration to the discrete `{-1, 0, 1}` model in `shard_parser.py`, these constants lose meaning. What replaces `CONFIDENCE_LOW_THRESHOLD` — just checking for `confidence == -1`?
- ~~`WIKI_ROUTING_MODEL` defaulted to `"claude-haiku-3-5"` — retired model name. Fixed to `claude-haiku-4-5-20251001`.~~ (fixed 2026-04-24)

## Changelog

| Date | Change | Why |
|---|---|---|
| 2026-04-24 | `WIKI_ROUTING_MODEL` default changed from `"claude-haiku-3-5"` to `"claude-haiku-4-5-20251001"` | Retired model name caused live API failures in the wiki ingest routing pass. |
