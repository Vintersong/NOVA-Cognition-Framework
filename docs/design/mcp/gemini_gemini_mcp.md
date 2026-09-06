# Gemini/gemini_mcp.py

**One-line purpose:** Gemini Flash worker tools (`gemini_execute_ticket`, `gemini_load_file`) registered into the NOVA MCP server.

## Why it exists

The Forgemaster sprint pipeline routes implementation tickets to Gemini Flash rather than Claude. `gemini_mcp.py` provides the two tools the orchestrator uses: one to execute a ticket (send a structured prompt to Gemini, optionally save output to a file) and one to read a file from disk as context. Both are registered into the existing MCPServer instance rather than running as a separate server.

## Key concepts

- **Lazy client init** — `get_client()` reads `GEMINI_API_KEY` at first tool use, not at server startup. This avoids stale-key issues when `.env` changes and means the key error is surfaced at use time, not startup.
- **Workspace boundary** — `gemini_execute_ticket` restricts file writes to `_WORKSPACE_DIR` (defaults to `{repo_root}/workspace/`). An env-overridden `GEMINI_OUTPUT_DIR` is accepted only if it is inside `_REPO_ROOT`.
- **Repo boundary** — `gemini_load_file` restricts reads to `_REPO_ROOT`. Paths outside the repo are rejected.
- **Markdown fence stripping** — `gemini_execute_ticket` strips leading/trailing ` ``` ` fences from Gemini's response before returning or writing — Gemini often wraps code output in fences even when instructed not to.
- **Model** — `MODEL` is imported from `config.GEMINI_MODEL` (default `gemini-2.5-flash`, overridable via `GEMINI_MODEL` env var). `sys.path` is patched at import time to allow the `mcp/` parent to be found.

## Public surface

- `register_gemini_tools(mcp)` — registers both tools onto an MCPServer instance.

**MCP tools** (2):
- `gemini_execute_ticket(ticket, context, output_file)` — send structured ticket to Gemini Flash; optionally write output to `output_file` in workspace.
- `gemini_load_file(filepath)` — read a repo file and return its content as a JSON string.

## Inputs and outputs

- **Reads:** files from `_REPO_ROOT` (via `gemini_load_file`).
- **Writes:** files to `_WORKSPACE_DIR` (via `gemini_execute_ticket` when `output_file` is set).
- **Env:** `GEMINI_API_KEY` (read from `mcp/Gemini/.env`, not repo root `.env`), `GEMINI_OUTPUT_DIR`.

## Invariants and assumptions

- The `.env` file is expected at `mcp/Gemini/.env` — a separate env file from the repo root `.env`. This is the only module with a non-standard `.env` location.
- Both tools call `is_blocked` / `denial_payload` from `permissions.py` before executing — consistent with all other tool modules.
- `output_file` is treated as a relative filename resolved against `_WORKSPACE_DIR`, not an absolute path — the tool cannot write to arbitrary paths even if the caller provides one.
- MCP tool annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) are set correctly: `gemini_execute_ticket` is `readOnlyHint=False`, `gemini_load_file` is `readOnlyHint=True`.

## Callers and integration

- `nova_server.py` — imports `register_gemini_tools` from `mcp/Gemini/gemini_mcp.py` and calls it at startup.
- `forgemaster_runtime.py` — does not call `gemini_execute_ticket` directly; it uses `_call_gemini` via the `google.genai` library directly.

## Known gaps / open questions

- ~~`MODEL` was hardcoded as `"gemini-2.5-flash"`, ignoring `config.GEMINI_MODEL` — fixed. Now imported from `config.GEMINI_MODEL`.~~ (fixed 2026-04-24)

## Changelog

| Date | Change | Why |
|---|---|---|
| 2026-04-24 | `MODEL` changed from hardcoded `"gemini-2.5-flash"` to `from config import GEMINI_MODEL as MODEL`. Added `sys.path` patch to resolve `mcp/config.py` from the `Gemini/` subdirectory. | `forgemaster_runtime.py` already respected `GEMINI_MODEL` env var — `gemini_execute_ticket` was the only tool that didn't. |
- `mcp/Gemini/.env` is a separate env file — easy to forget when rotating API keys. Two files to update.
- No streaming support — Gemini response is fully buffered before returning. Long generation tasks will hold the tool call open.
