# usage.py

**One-line purpose:** Append structured JSONL entries for every tool invocation to `nova_usage.jsonl`.

## Why it exists

Token cost and operation frequency tracking requires a persistent log that survives server restarts. `usage.py` extracts the log-write from `nova_server.py` so the log file path is configurable centrally via `config.USAGE_LOG_FILE` and the log format is consistent.

## Key concepts

- **JSONL format** — one JSON object per line: `{timestamp, tool, shards, metadata}`. Append-only, never truncated.
- **Error counter** — `_error_counts` tracks how many times `log_operation` has failed; logged via `logger.warning` but never propagates to callers.

## Public surface

- `log_operation(tool_name, shard_ids, metadata)` — append one entry; swallows all exceptions silently (logging failures must not break tool responses).

## Inputs and outputs

- **Writes:** `USAGE_LOG_FILE` (`nova_usage.jsonl` at repo root by default).
- **Env:** `NOVA_USAGE_LOG_FILE` (via `config.py`).

## Invariants and assumptions

- `log_operation` is fire-and-forget synchronous; no file locking — concurrent writes from multiple async tasks could produce interleaved lines on Windows (no atomic append). Unlikely in practice given single-process server.
- Ravens (`ravens.py`) write their own log entries directly to `nova_usage.jsonl` with `operator="HUGINN"` or `"MUNINN"` — they bypass `usage.log_operation` and write a different shape entry. The log is not uniform in structure.

## Callers and integration

- The handler modules call `log_operation` after the main work is done.
- `nott.py` — writes its own entries directly (not via `usage.log_operation`).
- `ravens.py` — writes its own entries directly (not via `usage.log_operation`).

## Known gaps / open questions

- Three different writers (`usage.py`, `nott.py`, `ravens.py`) produce different entry shapes to the same file — no schema enforcement.
- No rotation or size cap — `nova_usage.jsonl` grows unboundedly.
