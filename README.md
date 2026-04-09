# NOVA Cognition Framework

> **Persistent AI memory and multi-agent orchestration.**
> Conceived and authored by [Andrei Moldovean](https://github.com/Vintersong) — initial architecture documented April 2025; first public commit March 2026.

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-2.0-teal)](ROADMAP.md)
[![Author](https://img.shields.io/badge/author-Andrei_Moldovean-darkgreen)](https://github.com/Vintersong)

---

## What is NOVA?

NOVA is a **persistent memory system for AI agents**. It stores conversations and decisions as modular JSON **shards** — each with metadata, confidence scores, and time-based decay rates. A knowledge graph tracks relationships between shards so agents retrieve contextually relevant memory, not just recent history.

NOVA runs as an MCP server, meaning any MCP-compatible client (Claude Desktop, Claude Code, Cursor) can connect to it and query/write memory using structured tool calls.

**Core concepts coined in this project:**
- **Shard** — the atomic memory unit: structured JSON with confidence, decay, and graph edges
- **NÓTT** — the background daemon handling decay → compaction → merge → graph sync
- **HUGINN / MUNINN** — fast retrieval pass (Ravens module) and deep rerank pass
- **Forgemaster** — multi-agent orchestration layer built on top of NOVA

> 📄 See [`NOVA Framework.pdf`](NOVA%20Framework.pdf) and [`Executive Summary.pdf`](Executive%20Summary.pdf) for the original architecture documents (April 2025).

**Key capabilities:**
- Store and retrieve memory shards with semantic search
- Confidence-weighted recall with time-based decay
- Automatic deduplication and shard consolidation
- Knowledge graph of inter-shard relationships
- Usage logging for memory health monitoring

---

## What is Forgemaster?

Forgemaster is a **multi-agent orchestration layer** built on top of NOVA. It decomposes tasks into typed tickets and routes each to the optimal model in a defined hierarchy.

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
    └── Senior (Opus)           — cross-sprint decisions, major architecture
        └── Lead (Sonnet)       — per-sprint orchestration, review, ambiguous tickets
            └── Mid (Gemini Flash) — bounded implementation, boilerplate, structured output
                └── Low (local model) — trivial edits, formatting, deterministic tasks
```

Supporting roles routed by task type:
- Research / documentation → Claude Haiku
- UI / screen design → Stitch MCP

---

## How They Connect

```
You (design doc / feature request)
        │
        ▼
  Forgemaster Orchestrator  (Sonnet — reads forgemaster-orchestrator.md)
  ├── nova_shard_interact()  → load project context from NOVA
  ├── decompose into typed tickets
  ├── assign confidence scores → route by threshold
  │       ├── architecture / review / ambiguity  → Sonnet
  │       ├── implementation / boilerplate        → Gemini Flash (mcp_gemini_worker_gemini_execute_ticket)
  │       └── research / documentation            → Haiku (runSubagent)
  ├── parallel lanes execute (forgemaster-parallel-lanes.md)
  ├── QA pass (forgemaster-qa-review.md)
  ├── Lead review + bug fixes
  └── nova_shard_update()    → write decisions back to NOVA
```

---

## Prior Art & Attribution

This framework was designed and documented by **Andrei Moldovean** starting in early 2025. Key prior art:

| Document | Date | Description |
|---|---|---|
| [`NOVA Framework.pdf`](NOVA%20Framework.pdf) | April 2025 | Full architecture specification: shards, decay, graph, NÓTT daemon |
| [`Executive Summary.pdf`](Executive%20Summary.pdf) | April 2025 | Condensed system overview |
| [`NOVA Shard Memory Architectur.pdf`](NOVA%20Shard%20Memory%20Architectur.pdf) | April 2025 | Deep dive into shard memory model |
| [`Unified Conciousness Model.pdf`](Unified%20Conciousness%20Model.pdf) | April 2025 | Theoretical framework underlying the architecture |
| [First public commit](https://github.com/Vintersong/NOVA-Cognition-Framework/commit/94b483e2c419255b0bf5a0ec28ca23ed4e01441a) | March 2026 | Initial code release |

The specific naming conventions used in this project — **shard**, **NÓTT**, **HUGINN/MUNINN (Ravens)**, **Forgemaster**, **confidence decay**, **shard consolidation** — are original to this work.

---

## Quick Start

### 1. Install dependencies

```bash
cd mcp/
pip install -r requirements.txt
```

### 2. Configure environment

```bash
# mcp/.env — used by gemini_worker.py (standalone Gemini MCP server)
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-2.5-flash

# mcp/Gemini/.env — used by gemini_mcp.py (integrated into nova_server)
GEMINI_API_KEY=your_key_here
```

### 3. Register servers in Claude Desktop

`claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "nova": {
      "command": "python",
      "args": ["path/to/mcp/nova_server.py"],
      "env": { "NOVA_SHARD_DIR": "path/to/shards" }
    },
    "gemini_worker": {
      "command": "python",
      "args": ["path/to/mcp/gemini_worker.py"],
      "env": {
        "GEMINI_API_KEY": "your_key_here",
        "GEMINI_MODEL": "gemini-2.5-flash",
        "CONFIDENCE_THRESHOLD": "0.65"
      }
    }
  }
}
```

> VS Code auto-discovers Claude Desktop MCP servers via `chat.mcp.discovery.enabled`.
> Use `gemini-2.5-flash` — `gemini-2.5-pro` exhausts the free tier immediately.

### 4. Run the NOVA MCP server

```bash
python mcp/nova_server.py
```

---

## Directory Structure

```
NOVA-Cognition-Framework/
  mcp/
    nova_server.py              ← thin MCP adapter — 16 NOVA tools + Gemini tools
    config.py                   ← env vars: paths, thresholds, Norse Pantheon config
    schemas.py                  ← 15 Pydantic input models for all tool handlers
    store.py                    ← shard I/O and index management
    graph.py                    ← knowledge graph load/save/query/relate
    maintenance.py              ← confidence decay, compaction, cosine, merge
    usage.py                    ← operation logging to nova_usage.jsonl
    models.py                   ← UsageSummary, ShardRecord, PermissionDenial
    permissions.py              ← ToolPermissionContext (env-driven tool gating)
    session_store.py            ← session persistence
    forgemaster_runtime.py      ← sprint orchestration
    ravens.py                   ← HUGINN (fast retrieval) + MUNINN (deep rerank)
    nott.py                     ← NÓTT daemon: decay · compact · merge · graph sync
    hooks.py                    ← NovaHookRegistry / NovaHookEvent
    nova_embeddings_local.py    ← local all-MiniLM-L6-v2 embeddings + compaction
    gemini_worker.py            ← standalone Gemini Flash MCP server
    SKILL_v2.md                 ← agent cognitive instructions
    requirements.txt
    Gemini/
      gemini_mcp.py             ← Gemini tools registered into nova_server
    _deprecated/                ← v1 server, dev scripts, phase-change logs
  python/
    shard_index.py              ← index manager
    context_extractor.py        ← batch shard enrichment
    dedup_json.py               ← deduplicate shard content
    rename_shards.py            ← bulk rename shard files
  shards/                       ← live shard data (never edit directly)
  nova_sessions/                ← flushed MCP session state
  forgemaster/
    AGENTS.md                   ← agent roles, routing rules, model assignments
    SKILL_LIBRARY.md            ← index of all skills across 15 domains
    skills/                     ← core orchestration skills
    library/                    ← domain skill library (game-dev, engineering, etc.)
  built/                        ← compiled game project output
  docs/                         ← reference docs
  tools/
    chatgpt_to_nova.py          ← ChatGPT export → NOVA shard migration
  _deprecated/                  ← completed roadmap docs
```

Auto-generated (do not commit): `shard_index.json`, `shard_graph.json`, `nova_usage.jsonl`

---

## NOVA MCP Tools

| Tool | Description |
|---|---|
| `nova_shard_interact` | Load shards into context — HUGINN fast pass, MUNINN deep rerank |
| `nova_shard_create` | Create shard with post-write embedding enrichment |
| `nova_shard_update` | Append turn — auto-compaction + NÓTT hook |
| `nova_shard_search` | Confidence-weighted keyword + vector search |
| `nova_shard_list` | List all shards sorted by confidence |
| `nova_shard_get` | Read full shard — no side effects |
| `nova_shard_merge` | Merge shards into meta-shard, updates graph |
| `nova_shard_archive` | Soft-archive — excluded from search, preserved on disk |
| `nova_shard_forget` | Hard soft-delete with provenance log |
| `nova_shard_consolidate` | Full NÓTT cycle: decay → compact → merge suggestions |
| `nova_graph_query` | Query knowledge graph (direct or transitive BFS) |
| `nova_graph_relate` | Add directed relation between shards |
| `nova_session_flush` | Persist active session to disk |
| `nova_session_load` | Restore flushed session to memory |
| `nova_session_list` | List all persisted session IDs |
| `nova_forgemaster_sprint` | Full 4-turn sprint pipeline |
| `gemini_execute_ticket` | Execute a Forgemaster ticket via Gemini Flash |
| `gemini_load_file` | Load a file as Gemini context |

---

## Module Architecture

`nova_server.py` is a thin MCP adapter (~970 lines). All business logic lives in dedicated modules:

| Module | Responsibility |
|---|---|
| `config.py` | Single source for all env vars and defaults |
| `schemas.py` | 15 Pydantic input models for tool handlers |
| `store.py` | Shard filesystem I/O, index management, fragment extraction |
| `graph.py` | Knowledge graph load/save/query/relate/transitive BFS |
| `maintenance.py` | Confidence decay, auto-compaction, cosine similarity, merge candidates |
| `usage.py` | JSONL operation logging |
| `ravens.py` | HUGINN (Haiku-powered fast retrieval) · MUNINN (Sonnet-powered deep rerank) |
| `nott.py` | NÓTT daemon — scheduled decay + compact + merge + graph sync |
| `hooks.py` | `NovaHookRegistry` — fire-and-forget event dispatch (`SESSION_START`, `POST_SPRINT`, `COUNT_THRESHOLD`) |
| `permissions.py` | `ToolPermissionContext` — env-driven tool allow/deny |
| `session_store.py` | Session CRUD and flush-to-disk |
| `forgemaster_runtime.py` | Sprint orchestration — routes tickets to model lanes |
| `nova_embeddings_local.py` | Local `all-MiniLM-L6-v2` embedding + structured compaction summary |
| `models.py` | `UsageSummary`, `ShardRecord`, `PermissionDenial` data classes |

---

## Forgemaster File Placement Convention

```
built/
  <project-name>/
    README.md               ← project status, known bugs, design doc reference
    design/
      gdd/                  ← game design documents
      art/                  ← asset specs, style guides
    src/
      <language>/           ← e.g. Lua/, GDScript/
    production/
      sprints/              ← sprint plans
      milestones/           ← milestone docs
```

Agents must not place source files at the repo root or in flat unstructured directories. Every project output must have a `README.md` at its root. Sprint artifacts go in `production/sprints/`, never inline in source.

---

## Notes

- Never manually edit files in `shards/` — always use the MCP tools
- `shard_index.json`, `shard_graph.json`, and `nova_usage.jsonl` are auto-generated — do not commit
- `.env` files contain API keys — never commit them
- `_deprecated/` folders contain v1 code and dev artifacts — reference only
