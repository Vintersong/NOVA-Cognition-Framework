# hooks.py

**One-line purpose:** Lightweight event-driven hook registry — fire-and-forget async task dispatch for NOVA's maintenance triggers.

## Why it exists

Before `hooks.py`, `nova_server.py` called `asyncio.create_task(...)` inline in tool handlers to trigger maintenance. Scattering `create_task` calls made it hard to add new triggers or change what fires on each event. `NovaHookRegistry` replaces that pattern with a clean event → handler mapping, inspired by OpenHarness `HookExecutor`.

## Key concepts

- **`NovaHookEvent`** — four events: `SESSION_START` (fires on `nova_shard_interact`), `PRE_TOOL_USE` (reserved, not yet wired), `POST_SPRINT` (fires after `nova_shard_update`), `COUNT_THRESHOLD` (fires when shard count exceeds `NOTT_COUNT_THRESHOLD`).
- **`emit`** — fire-and-forget: schedules all handlers as `asyncio.create_task`. Never blocks the calling tool. Silently skips if there is no running event loop (e.g. sync test context).
- **`emit_wait`** — awaits all handlers in sequence. Used by `nova_shard_consolidate` where the result must be returned in the same tool call.

## Public surface

- `NovaHookRegistry()` — constructor; creates empty handler list per event.
- `registry.register(event, handler)` — attach an async handler.
- `registry.emit(event, **kwargs)` — fire-and-forget; safe to call from any async context.
- `registry.emit_wait(event, **kwargs)` — await all handlers; use for explicit user requests.

## Inputs and outputs

- **Reads/writes:** nothing directly. Handlers registered into the registry own their own I/O.

## Invariants and assumptions

- Handlers must be `async def` functions accepting `**kwargs` — the registry passes keyword arguments through to every handler.
- `PRE_TOOL_USE` is defined in the enum but has no handlers registered — it is a placeholder for future use.
- `emit` catches `RuntimeError` (no event loop) silently — this is intentional to allow the same code to run in sync test contexts without crashing.

## Callers and integration

- `server_context.py` — `ServerContext.bootstrap()` constructs the hook registry and registers the NÓTT lambdas for `SESSION_START`, `POST_SPRINT` and `COUNT_THRESHOLD`. Handler modules emit through `ctx`.

## Known gaps / open questions

- `PRE_TOOL_USE` is defined but never wired — could be used for permission checks or usage logging in future.
- No handler error handling — if a handler raises, the exception propagates silently or surfaces in the asyncio event loop's exception handler depending on whether `emit` or `emit_wait` was used.
