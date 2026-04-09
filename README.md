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

NOVA is a persistent memory system for AI agents. It stores conversations and decisions as modular JSON "shards" — each with metadata, confidence scores, and decay rates. A knowledge graph tracks relationships between shards so agents can retrieve contextually relevant memory, not just recent history.

NOVA runs as an MCP server, meaning any MCP-compatible client (Claude Desktop, Claude Code, Cursor) can connect to it and query/write memory using structured tool calls.

**Key capabilities:**
- Semantic shard retrieval via HUGINN (Haiku fast pass) + MUNINN (Sonnet deep rerank)
- Confidence-weighted recall with time-based decay
- Automatic compaction, deduplication, and consolidation
- Knowledge graph of inter-shard relationships
- Usage logging for memory health monitoring

---

## What is Forgemaster?

Forgemaster is a multi-agent orchestration layer built on top of NOVA. It decomposes tasks into typed tickets and routes each to the optimal model in a defined hierarchy.

Agents run in parallel sandboxed lanes. NOVA is Forgemaster's memory backplane: every sprint reads project context from shards and writes decisions back.

**Key capabilities:**
- Task decomposition from design docs into typed, routable tickets
- Model routing by task type and confidence score
- Parallel sandboxed execution lanes
- Human-in-the-loop at design, plan approval, and review stages
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
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.5-flash
CONFIDENCE_THRESHOLD=0.65
```

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
        "ANTHROPIC_API_KEY": "sk-ant-...",
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
    nova_server.py           ← ACTIVE MCP server (18 tools)
    config.py                ← all env vars and constants
    schemas.py               ← Pydantic input models
    store.py                 ← shard I/O and index management
    graph.py                 ← knowledge graph ops
    maintenance.py           ← decay, compaction, merge
    permissions.py           ← env-driven tool gating
    session_store.py         ← session persistence
    forgemaster_runtime.py   ← sprint orchestration
    ravens.py                ← HUGINN + MUNINN retrieval pipeline
    nott.py                  ← NOTT daemon: decay, compact, merge, graph sync
    nova_embeddings_local.py ← local all-MiniLM-L6-v2 embeddings
    gemini_worker.py         ← standalone Gemini Flash MCP server
    SKILL.md                 ← NOVA cognitive instructions
    requirements.txt
    Gemini/
      gemini_mcp.py          ← Gemini tools registered into nova_server
  utilities/
    chatgpt_to_nova.py       ← ChatGPT export migration
    shard_index.py           ← rebuild shard index manually
    dedup_json.py            ← duplicate shard detection
    autoresearch.py          ← automated research loop
    shard_compact.py         ← manual compaction helper
    theme_analyzer.py        ← theme distribution analysis
  shards/                    ← live shard data (never edit directly)
  nova_sessions/             ← flushed MCP session state
  output/                    ← built artifacts (games, experiments)
  forgemaster/
    AGENTS.md                ← orchestration config and model routing
    SKILL_LIBRARY.md         ← index of all skills across 15 domains
    STANDARDS.md             ← authoring standard for all forgemaster content
    skills/                  ← core orchestration skills (10 files)
    library/                 ← domain skill library (208 files, 13 categories)
    agents/                  ← agent persona definitions (320 files, 19 divisions)
  docs/                      ← reference and roadmap documents
  Donors/                    ← reference implementations
  .env                       ← API keys (never commit)
  .env.example               ← template (committed)
```

---

## NOVA MCP Tools (18)

| Tool | Description |
|---|---|
| `nova_shard_interact` | Load shards into context — HUGINN fast pass, MUNINN deep rerank |
| `nova_shard_create` | Create shard with post-write embedding enrichment |
| `nova_shard_update` | Append turn — auto-compaction triggered at threshold |
| `nova_shard_search` | Confidence-weighted keyword + vector search |
| `nova_shard_index` | Rebuild or inspect the shard index |
| `nova_shard_summary` | Summarise shard contents |
| `nova_shard_list` | List all shards sorted by confidence |
| `nova_shard_get` | Read full shard — no side effects |
| `nova_shard_merge` | Merge shards into meta-shard, updates graph |
| `nova_shard_archive` | Soft-archive — excluded from search, preserved on disk |
| `nova_shard_forget` | Hard exclude with provenance log |
| `nova_shard_consolidate` | Full maintenance cycle: decay + compact + merge suggestions |
| `nova_graph_query` | Query knowledge graph (direct or transitive BFS) |
| `nova_graph_relate` | Add directed relation between shards |
| `nova_session_flush` | Persist active session to disk |
| `nova_session_load` | Restore flushed session to memory |
| `nova_session_list` | List all persisted session IDs |
| `nova_forgemaster_sprint` | Full 4-turn sprint pipeline |

Gemini tools (via `mcp/Gemini/gemini_mcp.py`): `gemini_execute_ticket`, `gemini_load_file`

---

## Module Architecture

`nova_server.py` is a thin MCP adapter. All business logic lives in dedicated modules:

| Module | Responsibility |
|---|---|
| `config.py` | Single source for all env vars and defaults |
| `store.py` | Shard filesystem I/O, index management |
| `graph.py` | Knowledge graph load/save/query/relate/transitive BFS |
| `maintenance.py` | Confidence decay, auto-compaction, cosine similarity, merge candidates |
| `ravens.py` | HUGINN (Haiku fast retrieval) + MUNINN (Sonnet deep rerank) |
| `nott.py` | NOTT daemon — scheduled decay, compact, merge, graph sync |
| `permissions.py` | Env-driven tool allow/deny |
| `session_store.py` | Session CRUD and flush-to-disk |
| `forgemaster_runtime.py` | Sprint orchestration — routes tickets to model lanes |
| `nova_embeddings_local.py` | Local embedding generation and compaction summaries |

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

- Never manually edit files in `shards/` — always use the MCP tools
- `shard_index.json`, `shard_graph.json`, and `nova_usage.jsonl` are auto-generated — do not commit
- `.env` contains API keys — never commit it
- See `CLAUDE.md` for operational instructions and sprint workflow
