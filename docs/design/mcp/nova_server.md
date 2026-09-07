# nova_server.py

**One-line purpose:** MCPServer entry point — registers all 41 NOVA MCP tools, constructs all module-level singletons, and wires the hook/maintenance pipeline.

## Why it exists

`nova_server.py` is the single assembly point: it imports tool handlers from `store.py`, `graph.py`, `maintenance.py`, `ravens.py`, `nott.py`, `session_store.py`, `forgemaster_runtime.py`, and registers the external tool modules (`wiki_tools.py`, `nidhogg.py`, `evolve.py`, `Gemini/gemini_mcp.py`) via their `register_*_tools(mcp)` functions. Nothing else in `mcp/` imports from `nova_server.py` directly — dependency flows inward.

## Key concepts

- **MCPServer** — the MCP server framework (SDK v2). `mcp = MCPServer("nova_mcp_v2")` is the instance all tools are registered onto.
- **Module-level singletons** — constructed once at server startup:
  - `_huginn: Huginn`, `_muninn: Muninn` — retrieval agents (ravens.py)
  - `_nott: Nott` — maintenance daemon (nott.py)
  - `_hooks: NovaHookRegistry` — event registry (hooks.py)
  - `_session_store: SessionStore` — session persistence (session_store.py)
  - `_permission_ctx: ToolPermissionContext` — from env vars (permissions.py)
- **`_ALL_TOOL_NAMES`** — a tuple of all 30 tool names; consumed by `utilities/check_tool_docs.py` for documentation completeness checking.
- **Startup sequence** — `.env` is loaded before `config.py` imports (via `load_dotenv` at the top of the file). `prewarm_embedding_model()` is called to start loading the embedding model in a background thread. NÓTT and hooks are wired after all singletons are ready.
- **Post-write hooks** — `nova_shard_create` and `nova_shard_update` call `enrich_shard` synchronously and emit `POST_SPRINT` / `COUNT_THRESHOLD` hooks asynchronously after writing.

## Public surface

18 core tool handlers (async functions decorated with `@mcp.tool`):
- `nova_shard_interact`, `nova_shard_create`, `nova_shard_update`, `nova_shard_search`
- `nova_shard_index`, `nova_shard_summary`, `nova_shard_list`, `nova_shard_get`
- `nova_shard_merge`, `nova_shard_archive`, `nova_shard_forget`, `nova_shard_consolidate`
- `nova_graph_query`, `nova_graph_relate`
- `nova_session_flush`, `nova_session_load`, `nova_session_list`
- `nova_forgemaster_sprint`

Plus 12 tools registered from external modules (see CLAUDE.md tool table).

## Inputs and outputs

- **Reads:** `SHARD_DIR/*.json`, `shard_index.json`, `shard_graph.json`, `nova_sessions/*.json`.
- **Writes:** same files; also `nova_usage.jsonl`.
- **Env:** loads `.env` from repo root at startup; all config consumed via `config.py`.

## Invariants and assumptions

- Never imports `shard_parser.py` — the new `.shard` format and `ShardDB` are completely disconnected from the live server. The entire server operates on old JSON + float confidence shards.
- `nova_shard_interact` fires `SESSION_START` → NÓTT decay pass. This is the heaviest call on every session open.
- `nova_shard_update` fires `POST_SPRINT` → NÓTT full cycle (decay + compact + merge + graph sync), and `COUNT_THRESHOLD` if shard count exceeds `NOTT_COUNT_THRESHOLD`.
- `SHARD_DIR` is exported as a module-level constant so `test_nova.py` can display it in the explorer header.

## Callers and integration

- `test_nova.py` — imports `nova_server` directly and calls `nova_shard_index`, `nova_shard_search`, `nova_shard_get` as async functions.
- No external callers — `nova_server.py` is the top of the import tree.

## Known gaps / open questions

- `shard_parser.py` migration not wired — all 30 tools use old JSON + float confidence. No read path for `.shard` files exists in the live server.
- `enrich_shard` is called synchronously in tool handlers — adds latency to `nova_shard_create` and `nova_shard_update` proportional to embedding model inference time (~10–100ms on CPU).
- `_ALL_TOOL_NAMES` tuple must be manually kept in sync as tools are added or removed.
