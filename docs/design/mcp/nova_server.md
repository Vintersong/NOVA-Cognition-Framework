# nova_server.py

**One-line purpose:** MCP server entry point — constructs the `ServerContext`, builds the `MCPServer` instance, and calls each tool module's `register_*_tools`. It registers no handlers of its own.

## Why it exists

`nova_server.py` is the assembly point and nothing more. It was once a god-object holding every handler and singleton; both were extracted. Handlers now live in thirteen modules, and the singletons live in `server_context.py`. What remains is wiring:

1. Load `.env` before `config.py` is imported, since config reads the environment at import time.
2. `ctx = ServerContext.bootstrap()` — builds the process-scoped singletons and the hook bus.
3. Construct `MCPServer` with the server's identity, instructions and middleware.
4. Call thirteen `register_*_tools(mcp, ...)` functions, plus `register_prompts(mcp)`.
5. Declare four read-only resources, two resource templates, and the completion handler.
6. `mcp.run()` on stdio.

Nothing in `mcp/` imports from `nova_server.py` — dependency flows inward.

## Key concepts

- **`MCPServer`** — SDK v2, spec revision 2026-07-28. The server declares `title`, `version` (read from package metadata, falling back to a constant), and `instructions`.
- **`SERVER_INSTRUCTIONS`** — sent to the client on every request. It states the "call `nova_shard_interact` first" contract, the annotation vocabulary, and the two error-envelope shapes. This is the only copy of that contract clients actually receive; `nova://skill` carries the long form.
- **Middleware** — `mark_failed_results` (`result_middleware.py`) sets `isError` on any tool result whose payload reports a failure, and records the live request in `active_request` so the capability gate can reach the client session to ask for approval.
- **`ServerContext`** — all singletons (ravens, NÓTT, hook registry, session store, permission context, capability gate, audit log, usage counters, active skill, session id) hang off `ctx`. Handlers read through it rather than module globals.
- **`_ALL_TOOL_NAMES`** — derived from `tool_registry.all_names()`, not maintained by hand.
- **Error vocabulary for resources** — `ResourceNotFoundError` for a missing instance (`-32602`), `ResourceError` for a refusal. Neither is a crash, so neither logs a traceback. Returning a string such as `"SKILL.md not found."` would be served to the client as content.

## Public surface

No tool handlers. Registration calls, in order:

| Module | Tools |
|---|---|
| `gemini_mcp` | 2 — receives `gate=` and `audit_log=` explicitly |
| `nidhogg` | 3 |
| `evolve` | 1 |
| `huginn_tools` | 1 |
| `wiki_tools` | 6 |
| `facts` | 2 |
| `external_retrieval` | 1 |
| `calibrate` | 1 |
| `code_index` | 1 |
| `shard_tools` | 16 — returns the handler map re-exported for `utilities/test_nova.py` |
| `graph_tools` | 2 |
| `session_tools` | 3 |
| `forgemaster_tools` | 2 |

Four resources, each publishing name, title, description and MIME type: `nova://skill` (`text/markdown`), `nova://index`, `nova://graph`, `nova://usage` (all `application/json`).

Two resource templates: `nova://shard/{shard_id}` (`application/json`) and `nova://wiki/{slug}` (`text/markdown`). Both parameters are completable — `complete_argument` serves shard ids from the SQLite index's primary key and wiki slugs from the schema plus the pages on disk. A `{param}` matches a single URI segment, so a traversal never reaches a handler; `wiki.load_wiki_page` guards the tool path, where the slug is unconstrained.

Twenty prompts, registered by `prompts.register_prompts`: six hand-written workflow openers and one generated per file in `forgemaster/skills/`.

Plus `get_permitted_tools(permission_context)` — the tool names not blocked by the active permission context.

`_require(tool_name)` is the one piece of policy in this file. Resources are not tools, so nothing routes them through the permission context; a resource that serves the same data as a gateable tool calls `_require` with that tool's name. `nova://skill` and `nova://usage` mirror no tool and are deliberately open.

## Inputs and outputs

- **Reads:** `SHARD_DIR/*.json`, `shard_index.json`, `shard_graph.json`, `nova_sessions/*.json`.
- **Writes:** the same, plus `nova_usage.jsonl`.
- **Env:** loads `.env` from the repo root at startup; all config is consumed through `config.py`.

## Invariants and assumptions

- Runs on **stdio**. `docker/entrypoint.sh` and `.vscode/mcp.json` both spawn it as `python mcp/nova_server.py`, which puts `mcp/` on `sys.path[0]` — the bare imports (`from config import ...`) depend on that.
- Requires **SDK v2**. `mcp.server.mcpserver` does not exist in v1, and v2's `mcp.server.fastmcp` exists only to raise a migration error, so the pin is bounded on both sides.
- `prewarm_embedding_model()` starts the embedding model loading in a background thread at import.
- `nova_shard_interact` fires `SESSION_START` → a NÓTT decay pass. It is the heaviest call on session open.
- `nova_shard_update` fires `POST_SPRINT` → a full NÓTT cycle, and `COUNT_THRESHOLD` when the shard count exceeds `NOTT_COUNT_THRESHOLD`.

## Callers and integration

- `utilities/test_nova.py` — imports `nova_server` and calls the re-exported `nova_shard_index` / `nova_shard_search` / `nova_shard_get` handlers directly.
- `utilities/dump_tool_manifest.py` — boots the server to render the published tool and resource surface.
- Otherwise nothing: `nova_server.py` is the top of the import tree.

## Known gaps / open questions

- `enrich_shard` is dispatched to an executor rather than awaited, but still adds latency to `nova_shard_create` / `nova_shard_update` proportional to embedding inference.
- All 41 tools publish an `outputSchema`, but it is the degenerate `{"result": string}` wrapper the SDK generates for a handler annotated `-> str`; `structuredContent` therefore carries the JSON as an escaped string. Replacing it with real per-tool schemas is outstanding.
- Every tool's `inputSchema` nests the real arguments under a single `params` property, because every handler takes one Pydantic model. Flattening would improve tool-call accuracy but is a breaking change to every call site.
- stdio only. A loopback-only streamable-HTTP transport is possible under v2 but not wired.
