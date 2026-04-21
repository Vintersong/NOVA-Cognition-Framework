# NOVA-Cognition-Framework

![MCP](https://img.shields.io/badge/MCP-server-6B47ED?style=flat-square&logo=anthropic&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)
![Gemini](https://img.shields.io/badge/Gemini-Flash-4285F4?style=flat-square&logo=google&logoColor=white)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
![Shard Health](https://github.com/Vintersong/NOVA-Cognition-Framework/actions/workflows/shard-health.yml/badge.svg)

> **Author:** Andrei Moldovean · conceived April 2025 · first public commit March 2026  
> Original concepts: shard memory architecture, HUGINN/MUNINN retrieval pipeline, NÓTT daemon, confidence decay, shard consolidation, Forgemaster orchestration.

---

A unified repository containing **NOVA** (persistent AI memory) and **Forgemaster** (multi-agent orchestration). NOVA is the memory layer. Forgemaster is the execution layer. They share one repo and one data store.

---

## What is NOVA?

NOVA is a persistent memory system for AI agents. It stores conversations and decisions as modular JSON "shards" — each with metadata, confidence scores, and decay rates. A knowledge graph tracks relationships between shards so agents can retrieve contextually relevant memory, not just recent history. A parallel **wiki layer** holds curated, evergreen pages ingested from external sources.

NOVA runs as an MCP server, meaning any MCP-compatible client (Claude Desktop, Claude Code, Cursor) can connect to it and query/write memory using structured tool calls.

**Key capabilities:**
- Semantic shard retrieval via HUGINN (Haiku fast pass) + MUNINN (Sonnet deep rerank)
- Confidence-weighted recall with time-based decay
- NÓTT background daemon: decay, compaction, merge suggestions, graph sync
- Knowledge graph of inter-shard relationships with transitive BFS
- Three-tier discovery: index → summary → full shard (via `summary_index.json`)
- Wiki layer for curated, non-decaying knowledge
- Nidhogg ingestion pipeline with full provenance tracking
- Self-evolution loop (`nova_evolve`) for health-driven planning cycles
- Usage logging per operation to `nova_usage.jsonl`

---

## What is Forgemaster?

Forgemaster is a multi-agent orchestration layer built on top of NOVA. It decomposes tasks into typed tickets and routes each to the optimal model in a defined hierarchy.

Agents run in parallel sandboxed lanes. NOVA is Forgemaster's memory backplane: every sprint reads project context from shards and writes decisions back.

**Key capabilities:**
- Task decomposition from design docs into typed, routable tickets
- Model routing by task type and confidence score, with complexity-keyword override
- Parallel sandboxed execution lanes
- Sprint pipeline writes implementer output to disk when the design doc names a `Target file:`
- Per-call JSONL event log via `FORGEMASTER_EVENT_LOG`
- Full NOVA integration for persistent project context across sessions

---

## Agent Hierarchy

```
Architect (you)
    └── Lead (Sonnet)       — orchestration, review, ambiguous tickets
        └── Mid (Gemini Flash)  — bounded implementation, boilerplate, structured output
            └── Low (Haiku)     — research, documentation, fast tasks
```

---

## How They Connect

```
You (design doc / feature request)
        │
        ▼
  Forgemaster Orchestrator  (Sonnet)
  ├── nova_shard_interact()    → load project context from NOVA
  ├── decompose into typed tickets
  ├── route by task type
  │       ├── architecture / review / ambiguity  → Sonnet
  │       ├── implementation / boilerplate        → Gemini Flash
  │       └── research / documentation            → Haiku
  ├── parallel lanes execute
  ├── QA pass + Lead review
  └── nova_shard_update()      → write decisions back to NOVA
```

---

## Quick Start

### 1. Install dependencies

```bash
cd mcp/
pip install -r requirements.txt
```

### 2. Configure environment

Copy `.env.example` to `.env` at the repo root and fill in your keys:

```
CLAUDE_API_KEY=sk-ant-...
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.5-flash
CONFIDENCE_THRESHOLD=0.65
```

If `CLAUDE_API_KEY` is absent, HUGINN and MUNINN silently fall back to local-only retrieval (token overlap + cosine over local embeddings).

### 3. Register in Claude Desktop

`claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "nova": {
      "command": "python",
      "args": ["path/to/mcp/nova_server.py"],
      "env": {
        "NOVA_SHARD_DIR": "path/to/shards",
        "CLAUDE_API_KEY": "sk-ant-...",
        "GEMINI_API_KEY": "..."
      }
    }
  }
}
```

### 4. Run the server

```bash
python mcp/nova_server.py
```

---

## Directory Structure

```
NOVA-Cognition-Framework/
  mcp/
    nova_server.py           ← ACTIVE MCP server (registers all 30 tools)
    config.py                ← all env vars and defaults (single source of truth)
    schemas.py               ← Pydantic input models for core + wiki tools
    models.py                ← shared dataclasses (UsageSummary)
    store.py                 ← shard I/O, index management, summary-index layer
    graph.py                 ← knowledge graph ops (entities, relations, transitive BFS)
    maintenance.py           ← confidence decay, compaction, cosine similarity, merge
    permissions.py           ← env-driven tool gating (NOVA_DENIED_TOOLS/PREFIXES)
    hooks.py                 ← event-driven hook registry (session/post-sprint/count)
    usage.py                 ← JSONL operation logging
    session_store.py         ← session persistence (flush/load/list)
    forgemaster_runtime.py   ← sprint orchestration + real LLM dispatch
    ravens.py                ← HUGINN (Haiku) + MUNINN (Sonnet) retrieval
    nott.py                  ← NÓTT daemon: decay, compact, merge, graph sync
    nova_embeddings_local.py ← local all-MiniLM-L6-v2 embeddings + summaries
    evolve.py                ← nova_evolve self-improvement loop
    nidhogg.py               ← nidhogg_ingest/scan/status tools
    wiki.py                  ← WikiPage model, CRUD, embedding index
    wiki_ingest.py           ← wiki ingestion pipeline
    wiki_tools.py            ← nova_wiki_* MCP tools
    build_summary_index.py   ← batch-build summary_index.json via Haiku
    test_nova.py             ← memory-explorer CLI (not pytest)
    requirements.txt
    SKILL.md                 ← NOVA cognitive instructions
    ONBOARDING.md            ← fresh-install flow (used when no shards exist)
    Gemini/
      gemini_mcp.py          ← Gemini tools registered into nova_server
  utilities/
    chatgpt_to_nova.py       ← ChatGPT export migration
    shard_index.py           ← manual index rebuild
    dedup_json.py            ← duplicate shard detection
    autoresearch.py          ← automated research loop
    shard_compact.py         ← manual compaction helper
    theme_analyzer.py        ← theme distribution analysis
  shards/                    ← live shard data (never edit directly)
  wiki/                      ← curated markdown pages with YAML frontmatter + [[wikilinks]]
  intake/                    ← drop zone for nidhogg_scan
  nova_sessions/             ← flushed MCP session state
  output/                    ← built artifacts, forgemaster event logs
  forgemaster/
    AGENTS.md                ← orchestration config and model routing
    SKILL_LIBRARY.md         ← index of all skills across 15 domains
    STANDARDS.md             ← authoring standard for all forgemaster content
    skills/                  ← core orchestration skills (10 files)
    library/                 ← domain skill library (208 files, 15 categories)
    agents/                  ← agent persona definitions (326 files, 18 divisions)
  docs/                      ← reference and roadmap documents
  Donors/                    ← reference implementations
  .env                       ← API keys (never commit)
  .env.example               ← template (committed)
```

---

## NOVA MCP Tools (30)

### Core shard + graph + session (18)

| Tool | Description |
|---|---|
| `nova_shard_interact` | Load shards into context — HUGINN fast pass, MUNINN deep rerank |
| `nova_shard_create` | Create shard with post-write embedding enrichment |
| `nova_shard_update` | Append turn — auto-compaction triggered at threshold |
| `nova_shard_search` | Confidence-weighted keyword + ravens search |
| `nova_shard_index` | Compact browse rows, metadata only |
| `nova_shard_summary` | Browse rows plus a short synopsis per shard |
| `nova_shard_list` | Full raw dump (legacy; prefer index/summary) |
| `nova_shard_get` | Read full shard — no side effects |
| `nova_shard_merge` | Merge shards into meta-shard, updates graph |
| `nova_shard_archive` | Soft-archive — excluded from search, preserved on disk |
| `nova_shard_forget` | Hard exclude with provenance log |
| `nova_shard_consolidate` | Full NÓTT cycle: decay + compact + merge suggestions |
| `nova_graph_query` | Query knowledge graph (direct or transitive BFS) |
| `nova_graph_relate` | Add directed relation between shards |
| `nova_session_flush` | Persist active session to disk |
| `nova_session_load` | Restore flushed session to memory |
| `nova_session_list` | List all persisted session IDs |
| `nova_forgemaster_sprint` | Full 4-turn sprint pipeline |

Relation types: `influences`, `depends_on`, `contradicts`, `extends`, `references`, `merged_from`.

### Wiki (6)

| Tool | Description |
|---|---|
| `nova_wiki_schema` | Inspect or edit the wiki page schema |
| `nova_wiki_ingest` | Ingest markdown/source into the wiki |
| `nova_wiki_query` | Search across wiki pages |
| `nova_wiki_get` | Read a single wiki page in full |
| `nova_wiki_list` | List wiki pages (optionally by category) |
| `nova_wiki_lint` | Lint wiki content for schema compliance |

Wiki pages live in `wiki/` as markdown with YAML frontmatter and `[[wikilinks]]`. They are evergreen — they do not decay — and are only created/updated via `nova_wiki_ingest`.

### Nidhogg — repo/document ingestion (3)

| Tool | Description |
|---|---|
| `nidhogg_ingest` | Ingest a single file by path |
| `nidhogg_scan` | Scan `intake/` and ingest all pending files |
| `nidhogg_status` | Show manifest of ingested file hashes |

Nidhogg is non-destructive: it appends a `nidhogg` block to matched shards and never rewrites existing fields. Idempotent via SHA256 manifest.

### Evolution (1)

| Tool | Description |
|---|---|
| `nova_evolve` | Run one self-evolution cycle: analyze → verify → commit → govern → plan |

> ⚠ **Side effect:** `nova_evolve` runs `git add` and `git commit` locally when tests pass. On test failure it runs `git checkout -- .` to roll back the staged changes. Nothing is pushed to a remote. Use `dry_run=true` if you just want the director prompt. If `mcp/` changed, it writes `.sdd/runtime/restart_requested`.

### Gemini (2)

Registered into `nova_server` via `mcp/Gemini/gemini_mcp.py`:

| Tool | Description |
|---|---|
| `gemini_execute_ticket` | Send a structured ticket to Gemini Flash |
| `gemini_load_file` | Load a file from disk as codebase context |

### MCP Resources

Read-only resources exposed alongside the tools:

- `nova://skill` — contents of `mcp/SKILL.md`
- `nova://index` — current shard index JSON
- `nova://graph` — current knowledge graph JSON
- `nova://usage` — last 100 log entries + session token totals

---

## Module Architecture

`nova_server.py` is a thin MCP adapter. All business logic lives in dedicated modules:

| Module | Responsibility |
|---|---|
| `config.py` | Single source for all env vars and defaults |
| `schemas.py` | Pydantic input models for core + wiki tools |
| `models.py` | Shared dataclasses (UsageSummary) |
| `store.py` | Shard filesystem I/O, index, summary-index layer, path-traversal guards |
| `graph.py` | Knowledge graph load/save/query/relate/transitive BFS |
| `maintenance.py` | Confidence decay, auto-compaction, cosine similarity, merge candidates |
| `permissions.py` | Env-driven tool allow/deny |
| `hooks.py` | Event-driven hook registry (SESSION_START, POST_SPRINT, COUNT_THRESHOLD) |
| `usage.py` | JSONL operation logging |
| `session_store.py` | Session CRUD and flush-to-disk |
| `forgemaster_runtime.py` | Sprint orchestration — routes tickets to model lanes, writes output |
| `ravens.py` | HUGINN (Haiku fast retrieval) + MUNINN (Sonnet deep rerank) |
| `nott.py` | NÓTT daemon — scheduled decay, compact, merge, graph sync |
| `nova_embeddings_local.py` | Local embeddings + heuristic compaction summaries |
| `evolve.py` | Self-evolution loop, adaptive governor, auto-commit |
| `nidhogg.py` | Document ingestion with provenance |
| `wiki.py` / `wiki_ingest.py` / `wiki_tools.py` | Wiki layer model, pipeline, and MCP tools |
| `build_summary_index.py` | Batch-build `summary_index.json` via Haiku |

---

## Key Environment Variables

| Variable | Default | Notes |
|---|---|---|
| `NOVA_SHARD_DIR` | `shards` | Path to shard JSON files |
| `CLAUDE_API_KEY` | — | Powers HUGINN + MUNINN + wiki + summary generation |
| `HUGINN_MODEL` | `claude-haiku-4-5-20251001` | Fast retrieval pass |
| `MUNINN_MODEL` | `claude-sonnet-4-6` | Deep rerank pass |
| `HUGINN_CONFIDENCE_THRESHOLD` | `0.7` | Score ≥ this skips MUNINN |
| `RAVEN_API_TIMEOUT` | `10` | Per-call LLM timeout (seconds) before local fallback |
| `GEMINI_API_KEY` | — | Required for Gemini worker |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Implementation lane model |
| `CONFIDENCE_THRESHOLD` | `0.65` | Below this, Gemini escalates to Sonnet |
| `NOVA_COMPACT_THRESHOLD` | `30` | Turns before auto-compaction |
| `NOVA_COMPACT_KEEP` | `15` | Recent turns retained after compaction |
| `NOVA_DECAY_RATE` | `0.05` | Confidence decay per 7-day period |
| `NOVA_DECAY_DAYS` | `7` | Days per decay period |
| `NOVA_MERGE_THRESHOLD` | `0.85` | Cosine similarity floor for merge suggestions |
| `NOVA_CONFIDENCE_LOW` | `0.4` | Below this → `low_confidence` tag |
| `NOVA_RECENT_DAYS` | `3` | Within N days → `recent` tag |
| `NOVA_STALE_DAYS` | `14` | Not accessed N days → `stale` tag |
| `NOTT_COUNT_THRESHOLD` | `100` | Shard count triggering NÓTT merge scan |
| `NOVA_DENIED_TOOLS` | — | Comma-separated tool names to block |
| `NOVA_DENIED_PREFIXES` | — | Comma-separated prefixes to block |
| `NOVA_SUMMARY_INDEX_FILE` | `summary_index.json` | Path to summary index |
| `NOVA_SUMMARY_MARKDOWN_FILE` | `summary_index.md` | Path to summary markdown |
| `NOVA_WIKI_DIR` | `wiki` | Wiki pages directory |
| `NOVA_WIKI_SCHEMA` | `wiki_schema.json` | Wiki schema file |
| `NOVA_WIKI_INDEX` | `wiki_index.json` | Wiki embedding index |
| `NOVA_WIKI_ROUTING_MODEL` | `claude-haiku-3-5` | Wiki ingest routing model |
| `NOVA_WIKI_SYNTHESIS_MODEL` | `claude-sonnet-4-6` | Wiki synthesis model |
| `NIDHOGG_INTAKE_DIR` | `intake` | Nidhogg drop zone |
| `NIDHOGG_MANIFEST_FILE` | `nidhogg_manifest.json` | Ingested-hash manifest |
| `NIDHOGG_SIMILARITY_THRESHOLD` | `0.55` | Shard-match threshold for annotation |
| `FORGEMASTER_EVENT_LOG` | — | Override path for sprint JSONL event log |

---

## Prior Art & Attribution

Original architecture documented before any public commit. These documents establish authorship of the core concepts:

| Document | Date |
|---|---|
| NOVA Framework (concept doc) | April 2025 |
| Executive Summary | April 2025 |
| NOVA Shard Memory Architecture | April 2025 |
| Unified Consciousness Model | April 2025 |
| First public commit | March 2026 |

Original named concepts in this repository: **shard** (memory unit), **HUGINN/MUNINN** (retrieval pipeline), **NÓTT** (maintenance daemon), **confidence decay**, **shard consolidation**, **Forgemaster** (orchestration layer).

---

## Notes

- Never manually edit files in `shards/` or `wiki/` — always use the MCP tools
- Auto-generated files, never commit: `shard_index.json`, `shard_graph.json`, `summary_index.json`, `summary_index.md`, `wiki_index.json`, `nidhogg_manifest.json`, `nova_usage.jsonl`, `evolve_cycles.jsonl`, `evolve.json`
- `.env` contains API keys — never commit it
- `test_nova.py` is a memory-explorer CLI, not a pytest suite — run it with `python mcp/test_nova.py`
- See `CLAUDE.md` for operational instructions and sprint workflow; see `docs/ROADMAP.md` for shipped-vs-planned split
