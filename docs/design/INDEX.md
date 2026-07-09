# NOVA Design Doc Index

One-page navigation for all design documents across `mcp/` and `utilities/`. Each entry links to the relevant doc, states the module's purpose, and names its key callers or dependencies.

---

## Review status — last updated 2026-04-27

### Utilities — complete
All 9 remaining docs reviewed. 2 deleted (`usage_rollup`, `test_shards`). 2 scripts fixed (`shard_compact`, `theme_analyzer`). 1 doc corrected (`check_tool_docs`). 4 accurate, no changes needed.

### MCP — complete (25 of 25 done)

**Reviewed:**
| Doc | Outcome |
|---|---|
| `build_summary_index` | Doc fix — clarified API key fallback is intentional |
| `config` | Fixed — `WIKI_ROUTING_MODEL` retired model name updated |
| `evolve` | **Parked** — needs decision on `budget_usd` tracking and `_auto_commit` scope |
| `forgemaster_runtime` | Fixed — `stitch` model removed, token count bug fixed |
| `gemini_gemini_mcp` | Fixed — `MODEL` now reads `config.GEMINI_MODEL` |
| `gemini_test_gemini` | Deleted — orphaned smoke test |
| `graph` | Accurate, no changes |
| `hooks` | Accurate, no changes |
| `maintenance` | Accurate, no changes (float confidence deferred) |
| `models` | Fixed — added `add_turn_exact` for real API token counts |
| `session_store` | Fixed — added `add_message_with_usage` |
| `nidhogg` | Fixed — added raw JSON bypass flag (`_match_shards` reads shard files directly, bypasses `shard_parser.py`) |
| `nova_embeddings_local` | Accurate, no changes |
| `nova_server` | Accurate, no changes |
| `nott` | Fixed — removed misplaced `[USER NARRATION NEEDED]` tag from Invariants (code behavior observable from source) |
| `permissions` | Accurate, no changes |
| `ravens` | Accurate, no changes (float confidence incompatibility correctly flagged) |
| `schemas` | Accurate, no changes (float `min_confidence` incompatibility correctly flagged) |
| `shard_parser` | Accurate, no changes |
| `store` | Accurate, no changes (float confidence throughout correctly flagged) |
| `test_nova` | Accurate, no changes (float confidence bands correctly flagged) |
| `usage` | Accurate, no changes |
| `wiki` | Accurate, no changes |
| `wiki_ingest` | Fixed — retired model note updated to "Fixed 2026-04-24" (matches config.py fix) |
| `wiki_tools` | Fixed — "fourth copy" corrected to "third copy" of cosine function (grep confirms 3 copies: `maintenance.py`, `ravens.py`, `wiki_tools.py`) |

**Deferred to a separate pass (all docs):**
Float confidence migration — every module that reads/writes `meta_tags.confidence` as a float will need updating when `shard_parser.py` is wired in. Flagged in individual docs.

---

## How the system fits together

```
                  ┌─────────────────────────────────────────────┐
                  │               nova_server.py                │  ← MCP entry point
                  │  (assembles all singletons, registers tools)│
                  └────────┬────────────────────────────────────┘
                           │ imports
        ┌──────────────────┼──────────────────────────────────────┐
        │                  │                                      │
  ┌─────▼──────┐   ┌───────▼───────┐   ┌──────────────┐   ┌─────▼───────────┐
  │  store.py  │   │   ravens.py   │   │  nott.py     │   │ session_store.py │
  │ (shard I/O)│   │ (HUGINN+MUNINN│   │ (maintenance │   │ (sprint sessions)│
  └─────┬──────┘   │  retrieval)   │   │  daemon)     │   └────────┬─────────┘
        │          └───────┬───────┘   └──────┬───────┘            │
        │                  │                  │                     │
  ┌─────▼──────┐   ┌───────▼────────┐  ┌─────▼──────────┐  ┌──────▼────────────────┐
  │  graph.py  │   │nova_embeddings │  │ maintenance.py │  │ forgemaster_runtime.py│
  │(knowledge  │   │ _local.py      │  │(decay, compact,│  │(4-turn sprint pipeline│
  │  graph)    │   │(MiniLM embeds) │  │ merge, cosine) │  │  LLM dispatch)        │
  └────────────┘   └────────────────┘  └────────────────┘  └───────────────────────┘

  ┌─────────────────────────────────────────────────────────────────────────────────┐
  │  Tool modules (registered into nova_server via register_*_tools(mcp))          │
  │                                                                                 │
  │  wiki_tools.py → wiki_ingest.py → wiki.py                                      │
  │  nidhogg.py                                                                     │
  │  evolve.py                                                                      │
  │  Gemini/gemini_mcp.py                                                           │
  └─────────────────────────────────────────────────────────────────────────────────┘

  ┌──────────────────────────────────────────────┐
  │  Foundation (imported by everything else)    │
  │  config.py  ←  schemas.py  ←  models.py      │
  │  permissions.py   hooks.py   usage.py         │
  └──────────────────────────────────────────────┘

  ┌──────────────────────────────────┐
  │  Migration target (not yet wired)│
  │  shard_parser.py                 │
  └──────────────────────────────────┘
```

**Data flow on a typical tool call:**

1. Claude calls a tool → `nova_server.py` handler
2. Handler validates input via `schemas.py` model
3. Checks `permissions.py` → calls `store.py` for shard I/O
4. `ravens.py` (HUGINN→MUNINN) scores candidates using `nova_embeddings_local.py`
5. `maintenance.py` functions run via `nott.py` (fire-and-forget through `hooks.py`)
6. `graph.py` updated; `usage.py` appended; result returned

---


### Architecture overview

| Doc | Purpose | Key callers |
|---|---|---|
| [NOVA_AI_ARCHITECTURE_SUMMARY](../NOVA_AI_ARCHITECTURE_SUMMARY.md) | AI-readable end-to-end architecture map covering runtime topology, data model, retrieval, maintenance, tools, Forgemaster, deployment, tests, and migration boundaries | New AI agents, maintainers, architecture reviews |

## MCP modules

### Foundation

| File | Purpose | Key callers |
|---|---|---|
| [config.py](mcp/config.md) | Single source of truth for all env vars and path constants | Every module in `mcp/` |
| [schemas.py](mcp/schemas.md) | Pydantic input models for all 30 MCP tools | `nova_server.py`, `test_nova.py` |
| [models.py](mcp/models.md) | `UsageSummary` frozen dataclass — tracks per-session token estimates | `session_store.py`, `nova_server.py`, `forgemaster_runtime.py` |

### Migration target — new shard format

| File | Purpose | Status |
|---|---|---|
| [shard_parser.py](mcp/shard_parser.md) | Defines the new `.shard` plaintext format + `ShardDB` (SQLite, discrete `{-1,0,1}` confidence) | **Not wired.** No module imports it yet. Migration blocker for everything that uses float confidence. |

### Shard storage

| File | Purpose | Key callers |
|---|---|---|
| [store.py](mcp/store.md) | All shard and index filesystem I/O — load, save, index rebuild, summary index, legacy keyword search | `nova_server.py`, `maintenance.py`, `nidhogg.py`, `evolve.py`, `utilities/shard_index.py` |
| [graph.py](mcp/graph.md) | Load/save/query `shard_graph.json` — directed relations between shards (`depends_on`, `influences`, `contradicts`, etc.) | `nova_server.py`, `nott.py` |

### Maintenance

| File | Purpose | Key callers |
|---|---|---|
| [maintenance.py](mcp/maintenance.md) | Confidence decay, auto-compaction, cosine similarity, merge candidate detection | `nott.py` (injected as callables), `nova_server.py`, `nidhogg.py` |
| [nott.py](mcp/nott.md) | NÓTT daemon — runs decay/compact/merge/graph-sync in a thread pool in response to hook events. Four trigger levels: `SESSION_START`, `POST_SPRINT`, `COUNT_THRESHOLD`, `SCHEDULED` | `nova_server.py` (constructs singleton, registers with `hooks.py`) |

### Embeddings and retrieval

| File | Purpose | Key callers |
|---|---|---|
| [nova_embeddings_local.py](mcp/nova_embeddings_local.md) | Local all-MiniLM-L6-v2 embedding backend — 384-dim vectors; no API key required; compaction summaries | `nova_server.py` (prewarm + post-write hook), `ravens.py`, `nidhogg.py`, `wiki_ingest.py`, `maintenance.py` |
| [ravens.py](mcp/ravens.md) | HUGINN (Haiku fast pre-filter) + MUNINN (Sonnet deep rerank) — two-pass shard retrieval with graceful local fallback | `nova_server.py` (constructs singletons; used in `nova_shard_interact` and `nova_shard_search`) |
| [build_summary_index.py](mcp/build_summary_index.md) | CLI — batch-builds `summary_index.json` and `summary_index.md` for bulk shard imports. Resumable. | Standalone CLI. Mirrors `nova_shard_summary` MCP tool. |

### Session and runtime

| File | Purpose | Key callers |
|---|---|---|
| [session_store.py](mcp/session_store.md) | `NovaSession` (frozen dataclass) + `SessionStore` (create/flush/load sprint sessions to disk) | `forgemaster_runtime.py`, `nova_server.py` |
| [forgemaster_runtime.py](mcp/forgemaster_runtime.md) | 4-turn sprint pipeline — orchestrator → planner → implementer (Gemini Flash) → reviewer (Sonnet). Routing table, JSONL event log, implementation file write. | `nova_server.py` (`nova_forgemaster_sprint` tool) |

### Infrastructure

| File | Purpose | Key callers |
|---|---|---|
| [permissions.py](mcp/permissions.md) | Env-driven tool gating — `ToolPermissionContext` checked before any handler runs | `nova_server.py` (sets active context at startup), `nidhogg.py`, `evolve.py`, `gemini_mcp.py` |
| [hooks.py](mcp/hooks.md) | Lightweight async event registry (`NovaHookEvent`) — fire-and-forget `emit` or blocking `emit_wait` | `nova_server.py` (registers NÓTT lambdas; emits on `SESSION_START`, `POST_SPRINT`, `COUNT_THRESHOLD`) |
| [usage.py](mcp/usage.md) | Append JSONL entries to `nova_usage.jsonl` for every tool invocation | `nova_server.py` (called in every tool handler). Note: `ravens.py` and `nott.py` also write directly, in different entry shapes. |

### Server entry point

| File | Purpose | Notes |
|---|---|---|
| [nova_server.py](mcp/nova_server.md) | FastMCP server — registers all 30 tools, constructs all singletons, wires the hook/maintenance pipeline | Top of the import tree. Nothing else imports from here except `test_nova.py`. |

### Tool modules (registered via `register_*_tools(mcp)`)

| File | Purpose | MCP tools registered |
|---|---|---|
| [evolve.py](mcp/evolve.md) | Self-evolution loop — shard health analysis, pytest run, auto-commit, adaptive governor, PRODUCT DIRECTOR prompt | `nova_evolve` |
| [nidhogg.py](mcp/nidhogg.md) | Document ingestion — embed external files, cosine-match shards, append non-destructive `nidhogg` block | `nidhogg_ingest`, `nidhogg_scan`, `nidhogg_status` |
| [wiki.py](mcp/wiki.md) | `WikiPage` model, CRUD helpers, embedding index, schema management | (backend; no tools registered directly) |
| [wiki_ingest.py](mcp/wiki_ingest.md) | Two-pass ingest pipeline — Haiku routing + Sonnet synthesis — with prompt caching | (called by `wiki_tools.py`) |
| [wiki_tools.py](mcp/wiki_tools.md) | MCP tool handlers wrapping `wiki.py` and `wiki_ingest.py` | `nova_wiki_schema`, `nova_wiki_ingest`, `nova_wiki_query`, `nova_wiki_get`, `nova_wiki_list`, `nova_wiki_lint` |
| [Gemini/gemini_mcp.py](mcp/gemini_gemini_mcp.md) | Gemini Flash worker — execute implementation tickets, load repo files as context | `gemini_execute_ticket`, `gemini_load_file` |

### Developer tools

| File | Purpose | Notes |
|---|---|---|
| [test_nova.py](mcp/test_nova.md) | Interactive ASCII explorer — theme distribution, confidence health, spotlight shards, cross-theme search | CLI only (`cd mcp && python test_nova.py`). Misleadingly named — not a pytest suite. |

---

## Utilities

### Import and migration

| File | Purpose | Notes |
|---|---|---|
| [chatgpt_to_nova.py](utilities/chatgpt_to_nova.md) | Convert a ChatGPT export (`conversations-*.json`) to NOVA shards, one shard per conversation | One-shot migration CLI. Mentioned in `README.md` and `CLAUDE.md`. |
| [autoresearch.py](utilities/autoresearch.md) | Run a fixed topic list through Gemini Flash (with Google Search grounding) and write results as shards | Manual CLI. `RESEARCH_TOPICS` is hardcoded. Writes `turns` format, not `conversation_history`. |
| [autoresearch_loop/run.py](utilities/autoresearch_loop_run.md) | Self-directed scored research loop — local LM Studio model proposes queries, self-scores, keeps results above threshold | Manual CLI. Writes to `shards/autoresearch/` subdirectory (not picked up by flat `shard_index.py`). |

### Index and deduplication

| File | Purpose | Notes |
|---|---|---|
| [shard_index.py](utilities/shard_index.md) | Scan `shards/` and rebuild `shard_index.json` | Imported by `dedup_json.py`. Also referenced by several `mcp/` modules for the `SHARD_DIR` path constant. |
| [dedup_json.py](utilities/dedup_json.md) | Find and delete exact-duplicate shards by MD5-hashing their message content | Safe delete (no backup). Imports `shard_index.py` for `SHARD_DIR` and `update_index`. |

### Maintenance

| File | Purpose | Notes |
|---|---|---|
| [shard_compact.py](utilities/shard_compact.md) | Manually truncate long shard histories; `--fail-on-bloat` CI gate | Equivalent to `nova_shard_consolidate`'s compaction, but runnable without the server. Default now reads `$NOVA_SHARD_DIR` (fallback: `shards/`). Fixed 2026-04-24. |
| [theme_analyzer.py](utilities/theme_analyzer.md) | K-means cluster all shards by semantic similarity and write derived theme labels back to `meta_tags.theme` | Requires `scikit-learn`. Default now reads `$NOVA_SHARD_DIR` (fallback: `shards/`). Cluster map written to repo root, not shard dir. Fixed 2026-04-24. |

### Diagnostics

| File | Purpose | Notes |
|---|---|---|
| [check_tool_docs.py](utilities/check_tool_docs.md) | Verify that `CLAUDE.md`, `SKILL.md`, and `schemas.py` all cite the correct tool count from `nova_server.py` | Has `__main__` guard. No CI wiring — could be a pre-commit hook. |

---

## Cross-cutting concerns

### Float confidence migration

The system currently uses float confidence (0.0–1.0). `shard_parser.py` defines the new discrete `{-1, 0, 1}` model but **nothing routes to it yet**. Every module below will need updating before migration can complete:

- `store.py` — reads/writes float from `meta_tags.confidence`
- `maintenance.py` — decay formula is `confidence × (1 - decay_rate)` (float exponential; the primary blocker)
- `nott.py` — `_graph_sync` copies float confidence to graph entities
- `graph.py` — stores float confidence in entity records
- `ravens.py` — uses float as a score multiplier (negative value on `confidence = -1` would produce negative scores)
- `schemas.py` — `ShardIndexInput.min_confidence` is `float` 0.0–1.0
- `evolve.py` — `_shard_health` threshold `avg_confidence < 0.7` is meaningless post-migration
- `test_nova.py` — `confidence_band` thresholds 0.75/0.40 are float-based
- All utilities — `chatgpt_to_nova.py`, `autoresearch.py`, `autoresearch_loop/run.py`, `shard_compact.py`, `shard_index.py`, `theme_analyzer.py` all write float confidence

### Duplicated `cosine_similarity`

Three copies of the same function exist:
- `maintenance.py:cosine_similarity`
- `ravens.py:_cosine`
- `wiki_tools.py:_cosine`

Should be extracted to a shared utility (e.g., `mcp/math_utils.py`).

### Stale model name

~~`config.WIKI_ROUTING_MODEL = "claude-haiku-3-5"` is a retired model ID — fixed to `claude-haiku-4-5-20251001`.~~ (fixed 2026-04-24)

### `shard_parser.py` migration path

`shard_parser.py` is tested in `tests/test_shard_parser.py` but imported by nothing in production. Migration decisions resolved 2026-04-27:

- **Format coexistence (decided):** old `.json` shards stay in place; new `.shard` files are written for new shards. A compatibility reader detects format and handles both. No in-place conversion of existing shards.
- **`tier` field (deferred):** assignment during migration is not yet decided. Existing shards will receive a placeholder until a policy is agreed.
- **Parallel indexes (decided):** `ShardDB` (SQLite) is the write target for new shards; `shard_index.json` remains the read fallback. Both indexes coexist until a clean cutover is scheduled.

### `forgemaster_runtime.py` — `route_ticket` unused internally

`route_ticket` exists and is documented but `run_sprint` always uses the hardcoded `_ROLE_TO_MODEL` table. The routing table (`_ROUTING_TABLE`) is only callable externally.

### `gemini_mcp.py` — dual `.env` files

`Gemini/gemini_mcp.py` loads `mcp/Gemini/.env` separately from the repo-root `.env`. Rotating API keys requires updating two files.

---

## Brief files (prompts used to generate this doc set)

Archived under [`../archive/design/`](../archive/design/):
- `mcp_brief.md` — instructions used to generate all 25 `mcp/` design docs
- `utilities_brief.md` — instructions used to generate all 10 `utilities/` design docs
