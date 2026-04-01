# Phase 3 Changes — Forgemaster Runtime

## What was added

### `mcp/forgemaster_runtime.py`

A new `ForgemasterRuntime` class that orchestrates the Forgemaster sprint lifecycle using NOVA as its shared memory backplane.

**Key methods:**

| Method | Description |
|---|---|
| `__init__(session_store, permission_context)` | Accepts the module-level singletons from `nova_server_v2.py` |
| `bootstrap(sprint_id, shard_ids)` | Creates a `NovaSession`, loads shards into context |
| `route_ticket(task_type)` | Maps task types to model names via the routing table in `AGENTS.md` |
| `run_turn(session, role, skill_path, prompt)` | Executes one agent turn — reads skill file, appends to session, returns placeholder response |
| `run_sprint(sprint_id, design_doc, shard_ids)` | Full 4-turn pipeline (orchestrator → planner → implementer → reviewer) |
| `get_permitted_lanes(permission_context)` | Reports which agent roles are available given current tool permissions |

### `nova_forgemaster_sprint` MCP tool (in `mcp/nova_server_v2.py`)

A new MCP tool that exposes the full sprint lifecycle over the Model Context Protocol.

**Inputs:**

| Field | Type | Description |
|---|---|---|
| `sprint_id` | `str` | Unique identifier for this sprint (used as session ID) |
| `design_doc` | `str` | The design document or feature request to execute |
| `shard_ids` | `str \| None` | Optional comma-separated shard IDs to load into context |

**Output:** Sprint summary JSON with `sprint_id`, `turns`, `session_id`, `token_totals`, and `status`.

---

## Why

Phase 3 makes Forgemaster **executable for the first time**. Before this phase, the 4-turn orchestrator → planner → implementer → reviewer pipeline existed only as documentation (in `forgemaster/AGENTS.md` and the skill library). After this phase, it runs in code.

This is the architectural inflection point: you can now give Forgemaster a design document and have it autonomously plan, implement, review, and persist decisions — all wired through NOVA's memory layer.

---

## How `run_sprint()` uses Phase 1 and Phase 2

### Phase 1 (`ToolPermissionContext`)

- `ForgemasterRuntime.__init__` accepts a `ToolPermissionContext` instance
- `get_permitted_lanes()` uses `permission_context.blocks()` to flag agent roles as restricted when their required write tools are denied
- `nova_forgemaster_sprint` is gated by `_permission_context.blocks("nova_forgemaster_sprint")` — the same pattern used by all other NOVA tools

### Phase 2 (`NovaSession`, `SessionStore`)

- `bootstrap()` calls `_session_store.create(sprint_id)` to create a named, trackable sprint identity
- Each `run_turn()` call appends messages to the immutable `NovaSession` via `add_message()` and writes the updated session back via `_session_store.update()`
- `run_sprint()` calls `_session_store.flush(sprint_id)` at the end of the sprint, persisting the full session to disk
- Token totals from `session.usage` are included in the sprint summary

---

## What is stubbed vs. what is wired

| Capability | Status |
|---|---|
| Session creation and tracking | ✅ Fully wired (Phase 2 `SessionStore`) |
| Skill file loading from disk | ✅ Fully wired (reads markdown via `pathlib.Path`) |
| Permission gating | ✅ Fully wired (Phase 1 `ToolPermissionContext`) |
| Sprint flush to disk | ✅ Fully wired (`SessionStore.flush`) |
| Token usage tracking | ✅ Fully wired (accumulated via `NovaSession.add_message`) |
| LLM dispatch in `run_turn()` | 🔲 **Stubbed** — returns `"[{role} turn logged — skill: {skill_path}]"` |
| `nova_shard_update` at sprint end | 🔲 **Stubbed** — logged as intent, not executed |

The stubbing is intentional: Phase 3 proves the wiring works end-to-end. Phase 4 replaces the stubs with real model dispatch.

---

## What Phase 4 adds on top

- **Offline compaction CLI** — `nova_shard_consolidate` runnable as a scheduled job or CI step
- **Theme analyzer** — re-clusters the shard store periodically, making semantic search progressively smarter
- **Cluster map shard** — gives the orchestrator a structured view of its own knowledge before planning
- **Actual LLM dispatch** — replaces the `run_turn()` stubs with real model calls routed by `route_ticket()`

---

## How to invoke

Via any MCP client (Claude Desktop, Cursor, API):

```json
{
  "tool": "nova_forgemaster_sprint",
  "params": {
    "sprint_id": "my-project-sprint-1",
    "design_doc": "Build a roguelike with procedural dungeons and turn-based combat.",
    "shard_ids": "general_reflection,my_project_architecture"
  }
}
```

The tool returns a sprint summary:

```json
{
  "sprint_id": "my-project-sprint-1",
  "turns": 4,
  "session_id": "my-project-sprint-1",
  "token_totals": {
    "input_tokens": 842,
    "output_tokens": 16,
    "total_tokens": 858
  },
  "status": "complete"
}
```

The session is automatically flushed to `nova_sessions/my-project-sprint-1.json` at the end of the sprint. Reload it with `nova_session_load`.

---

## Dependency chain

```
Phase 1 — ToolPermissionContext, UsageSummary, ShardRecord, PermissionDenial
    ↓
Phase 2 — NovaSession, SessionStore, nova_session_flush/load/list
    ↓
Phase 3 — ForgemasterRuntime, nova_forgemaster_sprint  ← you are here
    ↓
Phase 4 — Offline compaction CLI, theme analyzer, real LLM dispatch
```
