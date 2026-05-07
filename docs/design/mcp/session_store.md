# session_store.py

**One-line purpose:** `NovaSession` (immutable session dataclass) and `SessionStore` (lifecycle manager: create, update, flush to disk, load from disk).

## Why it exists

Forgemaster sprint sessions need to accumulate message history and token usage across multiple LLM turns within a single tool call, then persist to disk so they can be restored in a later session. `NovaSession` uses the same frozen dataclass pattern as `UsageSummary` — every mutation returns a new instance, making concurrent access safe and state changes explicit.

## Key concepts

- **`NovaSession`** — frozen dataclass: `session_id`, `messages` (tuple of dicts), `usage` (UsageSummary), `created_at`, `last_active`. All fields are immutable.
- **`add_message(role, content)`** — returns a new `NovaSession` with the message appended, `last_active` updated, and `usage` incremented via `UsageSummary.add_turn`.
- **`SessionStore`** — manages the in-memory `_sessions` dict (active) and `{store_dir}/{session_id}.json` files (persisted). `flush` moves a session from memory to disk; `load` reverses this.
- **Session ID validation** — `_validate_session_id` enforces `SESSION_ID_PATTERN` from `config.py` on every public method. Prevents path traversal via session file names.

## Public surface

**`NovaSession`**:
- `NovaSession.new(session_id) → NovaSession` — factory; zero usage, empty messages.
- `NovaSession.from_dict(data) → NovaSession` — deserialise from JSON dict.
- `session.add_message(role, content) → NovaSession` — immutable update (word-count estimate).
- `session.add_message_with_usage(role, content, input_tokens, output_tokens) → NovaSession` — immutable update using exact API token counts.
- `session.to_dict() → dict` — serialise to JSON-compatible form.

**`SessionStore`**:
- `create(session_id) → NovaSession` — validate ID, create and register in memory.
- `get(session_id) → Optional[NovaSession]` — return active session or None.
- `update(session)` — replace in-memory session.
- `flush(session_id)` — write to disk (file-locked), remove from memory.
- `load(session_id) → NovaSession` — read from disk (file-locked), register in memory.
- `list_sessions() → list[str]` — stem names of all `*.json` files in store dir.

## Inputs and outputs

- **Reads/writes:** `{SESSION_STORE_DIR}/{session_id}.json` with `filelock` (5s timeout).
- **Env:** `SESSION_STORE_DIR` (via `config.py`).

## Invariants and assumptions

- `messages` is a `tuple` in memory, serialised as a `list` in JSON via `to_dict`.
- `add_message` calls `UsageSummary.add_turn` which counts words — not actual tokens. See `models.py` known gaps.
- `list_sessions()` returns disk-persisted sessions only (not active in-memory sessions) — a session that has been created but not yet flushed will not appear in this list.
- File lock timeout is 5 seconds — under heavy concurrent sprint load, lock contention could cause failures.

## Callers and integration

- `forgemaster_runtime.py` — creates, updates, and flushes sessions in `ForgemasterRuntime.run_sprint`.
- `nova_server.py` — constructs the module-level `_session_store` singleton; implements `nova_session_flush`, `nova_session_load`, `nova_session_list` MCP tools by delegating to `SessionStore`.

## Known gaps / open questions

- Sessions flushed to disk are not cleaned up automatically — stale sessions accumulate indefinitely in `nova_sessions/`.
- No maximum session message count — a very long sprint could create a large JSON file.
