# permissions.py

**One-line purpose:** Env-driven tool gating — a frozen `ToolPermissionContext` checked before any tool handler runs.

## Why it exists

Some deployment contexts need to disable subsets of tools (e.g. disable all write tools in a read-only audit environment, or block Nidhogg in restricted installations). `permissions.py` provides a single, testable place to enforce those restrictions without scattering `if os.getenv(...)` checks across tool handlers.

## Key concepts

- **`ToolPermissionContext`** — frozen dataclass with `denied_tools` (frozenset, exact names) and `denied_prefixes` (tuple). The `DEFAULT` class-level sentinel permits everything.
- **Module-level active context** — `_active` holds the process-wide permission context. `server_context.py` calls `set_active(...)` during `bootstrap()` with a context built from env vars. External tool modules (nidhogg, evolve, gemini) call `is_blocked(tool_name)` without importing the server.
- **Normalization** — `from_iterables` lowercases and strips whitespace from all tool names and prefixes at construction time, so env var values are case-insensitive.

## Public surface

- `ToolPermissionContext.from_iterables(deny_tools, deny_prefixes) → ToolPermissionContext` — factory from lists.
- `ToolPermissionContext.blocks(tool_name) → bool` — check a single tool name.
- `set_active(ctx)` — install a new process-wide context.
- `is_blocked(tool_name) → bool` — check the active context.
- `denial_payload(tool_name) → str` — canonical JSON error string for blocked tool responses.

## Inputs and outputs

- **Reads:** nothing at module level. `server_context.py` reads `NOVA_DENIED_TOOLS` and `NOVA_DENIED_PREFIXES` and passes the parsed lists to `from_iterables`.
- **Writes:** nothing.

## Invariants and assumptions

- `ToolPermissionContext.DEFAULT` is the class-level sentinel and is never `None`. External modules that import `is_blocked` before `set_active` is called will get the permissive default — no panic.
- Prefix matching is prefix-only (not glob, not regex) — `nova_shard` blocks `nova_shard_create`, `nova_shard_update`, etc.
- `denial_payload` returns a JSON string, not a Python dict — consistent with all other tool handlers which return `json.dumps(...)` strings.

## Callers and integration

- `server_context.py` — builds the context at bootstrap and calls `set_active`. Handler modules call `is_blocked` / `denial_payload`, or `gate_helpers.permission_error`, in their guards.
- `denial_payload` now returns the typed reject envelope (`status: "rejected"`, `code: "permission_denied"`), not a bare `{"error": ...}`.
- `nidhogg.py`, `evolve.py`, `gemini_mcp.py` — all call `is_blocked` and `denial_payload` at the top of their tool handler functions.

## Known gaps / open questions

- No granular per-caller permission (all tools share one global context for the process lifetime).
- Changing `NOVA_DENIED_TOOLS` or `NOVA_DENIED_PREFIXES` requires a server restart — no hot reload.
