# forgemaster_runtime.py

**One-line purpose:** `ForgemasterRuntime` — orchestrates the 4-turn sprint pipeline (orchestrator → planner → implementer → reviewer) with real LLM dispatch and file write support.

## Why it exists

`nova_forgemaster_sprint` needed a testable, injectable orchestration harness that could run real LLM calls, route each turn to the correct model, write implementation output to disk, and flush the session — without coupling to the MCP tool handler directly. `ForgemasterRuntime` accepts `SessionStore` and `ToolPermissionContext` as constructor arguments so it can be instantiated in tests with mock stores.

## Key concepts

- **Routing table** — `_ROUTING_TABLE` maps task types to model names; matches `forgemaster/AGENTS.md` preferred models section. Complexity override: tasks containing keywords from `_COMPLEX_KEYWORDS` are always routed to `MUNINN_MODEL` (Sonnet) regardless of type.
- **`_ROLE_TO_MODEL`** — the 4-turn pipeline model assignments: orchestrator → Sonnet, planner → Sonnet, implementer → Gemini Flash, reviewer → Sonnet.
- **`_call_anthropic` / `_call_gemini`** — both reload `.env` at call time so API key changes are picked up without restart. Both return `(text, input_tokens, output_tokens, latency_ms)`.
- **Implementation file write** — if the design doc contains a `Target file:` or `New file:` pattern, the implementer output is written to disk. Path escape protection via `is_relative_to(repo_root)`.
- **JSONL event log** — each LLM call is logged to `FORGEMASTER_EVENT_LOG` (env) or `output/forgemaster_runs/{sprint_id}.jsonl` by default. One entry per turn: timestamp, role, model, token counts, latency, error.

## Public surface

- `ForgemasterRuntime(session_store, permission_context)` — constructor.
- `bootstrap(sprint_id, shard_ids) → NovaSession` — create session, load shards as context.
- `route_ticket(task_type) → str` — returns model name for the task type.
- `run_turn(session, role, skill_path, prompt) → (NovaSession, str)` — execute one LLM turn, log event, return updated session and response text.
- `run_sprint(sprint_id, design_doc, shard_ids) → dict` — full 4-turn pipeline; writes implementation file if design doc names one; returns sprint summary.
- `get_permitted_lanes(permission_context) → list[str]` — returns roles available under a given permission context; restricted roles appear as `"implementer:restricted"`.

## Inputs and outputs

- **Reads:** skill files from `forgemaster/skills/*.md`; design doc from caller; API keys from `.env` at call time.
- **Writes:** implementation file to `{repo_root}/{target_rel}` if named; JSONL event log; flushes session to `nova_sessions/{sprint_id}.json`.
- **Env:** `FORGEMASTER_EVENT_LOG`, `CLAUDE_API_KEY`, `GEMINI_API_KEY`, `MUNINN_MODEL`, `HUGINN_MODEL`, `GEMINI_MODEL`.

## Invariants and assumptions

- `run_turn` calls `session.add_message_with_usage` for assistant turns, passing exact `in_tok` / `out_tok` from the API response. User turns still use `add_message` (word-count estimate — no real counts available pre-dispatch).
- `_write_implementation_file` uses Python 3.9+ `Path.is_relative_to`. A compatibility fallback exists for Python < 3.9.
- Skill files that do not exist produce `[skill file not found: ...]` text in the prompt instead of raising — the turn continues without the skill context.

## Callers and integration

- `forgemaster_tools.py` — the `nova_forgemaster_sprint` handler constructs a `ForgemasterRuntime` per call and calls `run_sprint`. The tool asks for approval first.

## Known gaps / open questions

- `route_ticket` is defined but not called during `run_sprint` — the 4 roles always use `_ROLE_TO_MODEL` fixed assignments. `route_ticket` is available for external callers but unused internally.
- `bootstrap` appends shard IDs as a stub acknowledgment message, not actual shard content — the shards are not loaded and injected into context. The intent may be for `nova_shard_interact` to have been called beforehand.
- No retry logic on LLM dispatch failure — `[DISPATCH FAILED: ...]` text becomes the response for that turn, which the next turn's model then receives as context.

## Changelog

| Date | Change | Why |
|---|---|---|
| 2026-04-24 | `ui`, `frontend`, `mockup` entries in `_ROUTING_TABLE` changed from `"stitch"` to `MUNINN_MODEL`. | `stitch` was a test placeholder for a UI model that was never defined. Claude Sonnet now handles design tasks. Would have raised `ValueError` if called. |
- ~~`stitch` model lane in `_ROUTING_TABLE` — was a placeholder, never implemented. `ui`, `frontend`, `mockup` tasks would have raised `ValueError`. Fixed: all three now route to `MUNINN_MODEL` (Sonnet).~~ (fixed 2026-04-24)
