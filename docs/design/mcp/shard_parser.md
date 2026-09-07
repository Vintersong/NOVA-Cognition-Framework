# shard_parser.py

**One-line purpose:** Defines the new `.shard` file format and a SQLite index (`ShardDB`) — the replacement for the old JSON + float confidence shard model.

## Why it exists

The old JSON shards used float confidence (0.0–1.0) which caused practical issues and was conceptually wrong: a fact is either confirmed (1), unknown (0), or contradicted (-1). `shard_parser.py` introduces a new file format and a proper SQLite-backed index that enforces the discrete model at the database constraint level.

## Key concepts

- **`.shard` file format** — plaintext file with `@@key: value` header lines, then a `---` separator, then freeform markdown content. No JSON.
- **Discrete confidence `{-1, 0, 1}`** — enforced by both `ShardParser` validation and the SQLite `CHECK` constraint. Floats are rejected.
- **`decay_rate`** — float per-shard; `0` means never decay. Decay transitions: `1→0` after `1/decay_rate` days, `0→-1` after `2/decay_rate` days.
- **Tier** — `personal | department | studio` — scoping system for multi-user contexts.
- **`ShardDB`** — SQLite wrapper that holds parsed shard metadata; supports upsert, query, decay, and reinforcement operations.

## Public surface

**`ShardParser`** (classmethod-only):
- `parse(file_path) → dict` — parse a `.shard` file; returns dict with `valid`, `errors`, and all fields.
- `write(file_path, shard) → bool` — serialize a shard dict back to `.shard` format.
- `_validate_and_normalize(data)` — validates all fields in-place; rejects float confidence, unknown tiers.

**`ShardDB`**:
- `__init__(db_path)` — opens/creates SQLite db, initialises schema.
- `upsert(shard) → bool` — insert or update a shard record.
- `add_file(file_path) → bool` — parse a `.shard` file and upsert it.
- `decay(now=None) → int` — apply time-based confidence decay to all shards; returns count of updated shards.
- `reinforce(shard_id) → bool` — step confidence up by 1 (capped at 1).
- `query(tier, confidence, topic_keyword) → list[dict]` — flexible query with optional filters.
- `close()` / context manager support.

## Inputs and outputs

- **Reads:** `.shard` files from any path passed to `parse()` or `add_file()`.
- **Writes:** `.shard` files via `write()`; SQLite database at `db_path`.
- **Env:** none consumed directly.

## Invariants and assumptions

- Confidence must be an integer in `{-1, 0, 1}`. Float values are coerced via `float → int` check; fractional floats are rejected.
- The `---` separator line is required; files without it are marked invalid.
- Header lines must start with `@@`; any other non-blank line in the header section is an error.
- `decay_rate=0` means the shard never decays (permanent).
- The SQLite `CHECK (confidence IN (-1, 0, 1))` enforces the constraint at the DB level.

## Callers and integration

`facts.py` imports `ShardDB` and opens it against `FACTS_INDEX_FILE`, backing the `nova_facts_search` and `nova_facts_rebuild` tools — so the `.shard` format is live, but as a **separate curated corpus** rather than as the primary shard store. The main shard path (`store.py`, `maintenance.py`, `nott.py`, `ravens.py`) still reads JSON with float confidence. `shard_parser.py` is tested in `tests/test_shard_parser.py`.

## Known gaps / open questions

- **Two formats coexist by design, for now.** The `.shard` corpus is reached through `facts.py`; the main shard store and every maintenance pass still use JSON + float confidence. The discrete epistemic encoding does reach the main store by another route — `nova_shard_db.py` indexes it and `nova_shard_query_state` queries it — so this is no longer a dead end, but a full migration of the primary store has not happened.
- What is the migration plan? Will old JSON shards be converted, or will the two formats coexist with a reader that handles both?
- `ShardDB` is in-memory SQLite (no WAL mode set) — concurrent write safety under multiple tool calls is not guaranteed.
- The `tier` field (`personal | department | studio`) is not present in the old JSON shard schema. How will it be assigned during migration?
