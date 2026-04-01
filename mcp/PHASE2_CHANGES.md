# Phase 2 Changes — Session Store

## What was added

### `mcp/session_store.py` (new file)

- **`NovaSession`** — a frozen dataclass representing a single Forgemaster sprint session.
  - Fields: `session_id`, `messages` (immutable tuple of `{"role", "content", "timestamp"}` entries),
    `usage` (a `UsageSummary` from Phase 1), `created_at`, `last_active`.
  - `add_message(role, content) -> NovaSession` — immutable update; returns a new instance with the
    message appended, usage updated, and `last_active` refreshed.
  - `to_dict() -> dict` — serialisable form for writing to disk.
  - `from_dict(data) -> NovaSession` — classmethod for loading from disk.

- **`SessionStore`** — manages active and persisted sessions.
  - `__init__(store_dir)` — creates the directory if absent; defaults to `nova_sessions/` next to
    the shard store.
  - `create(session_id) -> NovaSession` — creates and registers a new in-memory session.
  - `get(session_id) -> NovaSession | None` — returns the active in-memory session.
  - `update(session)` — replaces the in-memory entry.
  - `flush(session_id)` — writes `{store_dir}/{session_id}.json` (file-locked) and evicts from
    memory.
  - `load(session_id) -> NovaSession` — reads from disk into memory and returns the session.
  - `list_sessions() -> list[str]` — all session IDs currently on disk.

### `mcp/nova_server_v2.py` (updated)

- Imports `SessionStore` and `NovaSession` from `session_store`.
- Module-level `_session_store = SessionStore(...)` initialised at startup from
  `NOVA_SESSION_STORE_DIR` env var (default: `nova_sessions/` in the repo root).
- `ShardInteractInput` gains an optional `session_id: str | None = None` field.
- `nova_shard_interact`: when `session_id` is provided, calls `_session_store.get()` or
  `create()`, appends both the prompt and the response via `add_message()`, calls
  `_session_store.update()`, and includes `session_id` and `session_message_count` in the log
  entry written to `nova_usage.jsonl`.
- Three new MCP tools:
  - **`nova_session_flush`** — persists an active session to disk; returns JSON confirmation with
    token totals.
  - **`nova_session_load`** — loads a persisted session back into memory; returns session metadata
    and message count.
  - **`nova_session_list`** — returns the list of all persisted session IDs on disk.
- All three tools added to `_ALL_TOOL_NAMES` so they are subject to `ToolPermissionContext` gating
  from Phase 1.

---

## Why

Forgemaster sprints had no persistent identity. Every `nova_shard_interact` call was anonymous —
NOVA could not answer:

- Which interactions belong to the same sprint?
- What is the token cost of this sprint so far?
- How do I resume from exactly where I stopped if a lane crashes?

Phase 2 gives every Forgemaster sprint a **named, resumable identity**. The `session_id` parameter
threads a single label through all interactions in a sprint. When the sprint ends,
`nova_session_flush` persists the full message log and token totals to disk. The next sprint can
call `nova_session_load` to restore the prior session's context.

This directly backs the `forgemaster-nova-session-handoff` skill defined in
`forgemaster/skills/` — that skill described the handoff protocol in prose but had no executable
code. Phase 2 provides the executable layer for the first time.

---

## How to use

### Tracking a sprint under a named session

Pass `session_id` to `nova_shard_interact`:

```json
{
  "message": "load project context",
  "session_id": "forgemaster-sprint-042"
}
```

Every call with the same `session_id` appends messages to the same in-memory session and
accumulates token counts.

### Flushing a session to disk at sprint end

```json
{
  "tool": "nova_session_flush",
  "session_id": "forgemaster-sprint-042"
}
```

Returns:

```json
{
  "status": "flushed",
  "session_id": "forgemaster-sprint-042",
  "message_count": 12,
  "token_totals": {
    "input_tokens": 3140,
    "output_tokens": 2280,
    "total_tokens": 5420
  }
}
```

### Restoring a session at the next sprint start

```json
{
  "tool": "nova_session_load",
  "session_id": "forgemaster-sprint-042"
}
```

### Listing all persisted sessions

```json
{
  "tool": "nova_session_list"
}
```

---

## How Phase 2 builds on Phase 1

`NovaSession` wraps a `UsageSummary` (from `mcp/models.py`, Phase 1) to accumulate token counts
across all interactions in a sprint. Without Phase 1's `UsageSummary`, the session object would
have no token tracking. The `ToolPermissionContext` from Phase 1 gates the three new session tools
just like all other NOVA tools.

---

## Dependency chain

```
Phase 1: UsageSummary + ToolPermissionContext      ← merged into dev
    ↓  provides: token budget + access control
Phase 2: NovaSession + SessionStore                ← this PR
    ↓  provides: sprint identity + resumable state
Phase 3: ForgemasterRuntime turn loop
    ↓  provides: executable orchestrator → implementer → reviewer pipeline
Phase 4: shard_compact.py + theme_analyzer.py
    ↓  provides: offline maintenance + semantic auto-theming
```
