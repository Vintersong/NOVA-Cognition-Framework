# NOVA-Cognition-Framework

A unified repository containing **NOVA** (persistent AI memory) and **Forgemaster** (multi-agent orchestration). NOVA is the memory layer. Forgemaster is the execution layer. They share one repo and one data store.

---

## What is NOVA?

NOVA is a persistent memory system for AI agents. It stores conversations and decisions as modular JSON "shards" — each with metadata, confidence scores, and decay rates. A knowledge graph tracks relationships between shards so agents can retrieve contextually relevant memory, not just recent history.

NOVA runs as an MCP server, meaning any MCP-compatible client (Claude Desktop, Claude Code, Cursor) can connect to it and query/write memory using structured tool calls.

**Key capabilities:**
- Store and retrieve memory shards with semantic search
- Confidence-weighted recall with time-based decay
- Automatic deduplication and shard consolidation
- Knowledge graph of shard relationships
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
    └── Senior (Opus)          — cross-sprint decisions, major architecture
        └── Lead (Sonnet)      — per-sprint orchestration, review, ambiguous tickets
            └── Mid (Gemini Flash) — bounded implementation, boilerplate, structured output
                └── Low (local model) — trivial edits, formatting, deterministic tasks
```

Supporting roles routed by task type:
- **Research / documentation** → Claude Haiku
- **UI / screen design** → Stitch MCP

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
> Ensure `GEMINI_MODEL` is `gemini-2.5-flash` — `gemini-2.5-pro` exhausts the free tier immediately.

### 4. Run the NOVA MCP server

```bash
python mcp/nova_server.py
```

---

## Directory Structure

```
NOVA-Cognition-Framework/
  mcp/
    nova_server.py          ← active MCP server (18 tools incl. Gemini)
    gemini_worker.py        ← standalone Gemini Flash MCP server
    SKILL_v2.md             ← active cognitive instructions
    requirements.txt
    .env                    ← Gemini key for gemini_worker.py
    Gemini/
      gemini_mcp.py         ← Gemini tools module (registered into nova_server)
      .env                  ← Gemini key for gemini_mcp.py
    _deprecated/            ← v1 reference only, do not modify
  python/
    shard_index.py          ← index management
    context_extractor.py    ← batch semantic enrichment
  shards/                   ← live shard data (never modify directly)
  forgemaster/
    AGENTS.md               ← agent roles, routing rules, model assignments
    SKILL_LIBRARY.md        ← index of all 150+ skills across 15 domains
    skills/                 ← core orchestration skills
    library/                ← domain skill library (game-dev, engineering, etc.)
  game/
    README.md               ← game project docs and status
    Lua/platformer/         ← The Art of Becoming — Stage I prototype
  tools/
    chatgpt_to_nova.py      ← ChatGPT export migration
  shard_index.json          ← auto-generated (do not commit)
  shard_graph.json          ← auto-generated (do not commit)
  nova_usage.jsonl          ← auto-generated (do not commit)
  .env                      ← secrets (do not commit)
  .gitignore
  README.md
```

---

## NOVA MCP Tools (v2)

| Tool | Description |
|---|---|
| `nova_shard_interact` | Load shards into context by semantic relevance |
| `nova_shard_create` | Create new shard with post-write enrichment |
| `nova_shard_update` | Append to shard with auto-compaction |
| `nova_shard_search` | Search with confidence weighting |
| `nova_shard_list` | List all shards with confidence scores |
| `nova_shard_get` | Read full shard content, no side effects |
| `nova_shard_merge` | Merge shards into meta-shard |
| `nova_shard_archive` | Soft-archive (excludes from search) |
| `nova_shard_forget` | Hard delete with provenance log |
| `nova_shard_consolidate` | Run decay + compact + merge suggestion cycle |
| `nova_graph_query` | Query inter-shard knowledge graph |
| `nova_graph_relate` | Add directed relation between shards |
| `nova_session_flush` | Persist active sprint session to disk |
| `nova_session_load` | Restore stored session to memory |
| `nova_session_list` | List all stored session IDs |
| `nova_forgemaster_sprint` | Full 4-turn sprint pipeline |
| `gemini_execute_ticket` | Execute a Forgemaster ticket via Gemini Flash |
| `gemini_load_file` | Load a file from disk as Gemini context |

---

## Forgemaster File Placement Convention

When Forgemaster agents produce output files, they must follow this structure:

```
game/
  <project-name>/
    README.md               ← project status, known bugs, design doc reference
    design/
      gdd/                  ← game design documents
      art/                  ← asset specs, style guides
    src/
      <language>/           ← e.g. Lua/, GDScript/, src/
        <module>.lua
    production/
      sprints/              ← sprint plans
      milestones/           ← milestone docs
```

Agents must not place source files at the repo root or in unstructured flat directories. Every project output must have a `README.md` at its root. Sprint artifacts go in `production/sprints/`, never inline in source.

---

## Notes

- Never manually edit files in `shards/` — always go through the MCP tools
- `shard_index.json`, `shard_graph.json`, and `nova_usage.jsonl` are auto-generated and should not be committed
- `.env` files contain API keys — never commit them
- `_deprecated/` is v1 reference — do not modify or delete

---

## What is NOVA?

NOVA is a persistent memory system for AI agents. It stores conversations and decisions as modular JSON "shards" — each with metadata, confidence scores, and decay rates. A knowledge graph tracks relationships between shards so agents can retrieve contextually relevant memory, not just recent history.

NOVA runs as an MCP server, meaning any MCP-compatible client (Claude Desktop, Claude Code, Cursor) can connect to it and query/write memory using structured tool calls.

**Key capabilities:**
- Store and retrieve memory shards with semantic search
- Confidence-weighted recall with time-based decay
- Automatic deduplication and shard consolidation
- Knowledge graph of shard relationships
- Usage logging for memory health monitoring

---

## What is Forgemaster?

Forgemaster is a multi-agent orchestration layer built on top of NOVA. It decomposes tasks into typed tickets and routes each to the optimal model — Claude Sonnet for architecture and review, Gemini Flash for implementation and boilerplate, Claude Haiku for research and documentation.

Agents run in parallel sandboxed lanes and return results as PRs for human review. NOVA is Forgemaster's memory backplane: every sprint reads project context from shards and writes decisions back.

**Key capabilities:**
- Task decomposition from design docs
- Model routing by task type
- Parallel sandboxed execution lanes
- Human-in-the-loop at design, plan, and PR stages
- Full NOVA integration for persistent project context

---

## How They Connect

```
You (design doc / feature request)
        │
        ▼
  Forgemaster Orchestrator
  ├── loads NOVA shards (project context)
  ├── decomposes into typed tickets
  ├── routes tickets to optimal models
  |│       ├── claude-sonnet  → architecture, review
  |│       ├── gemini-flash   → implementation, boilerplate
  |│       └── claude-haiku   → research, documentation
  ├── agents execute in parallel lanes
  ├── results returned as PRs
  └── decisions written back to NOVA shards
```

---

## Quick Start

### 1. Install dependencies

```bash
cd mcp/
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env and set your OPENAI_API_KEY
```

### 3. Run the NOVA MCP server

```bash
python mcp/nova_server.py
```

### 4. Connect a client

Add to your MCP client config (e.g. Claude Desktop `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "nova": {
      "command": "python",
      "args": ["path/to/mcp/nova_server.py"]
    }
  }
}
```

---

## Directory Structure

```
NOVA-Cognition-Framework/
  mcp/
    nova_server.py          ← active MCP server (16 tools)
    SKILL_v2.md             ← active cognitive instructions
    requirements.txt
    nova_server.py          ← v1 reference (do not delete)
    SKILL.md                ← v1 reference (do not delete)
  python/
    shard_index.py          ← index management (used by v2)
    context_extractor.py    ← batch semantic enrichment
    dedup_json.py
    rename_shards.py
    main.py
  shards/                   ← live shard data (do not modify manually)
  forgemaster/
    AGENTS.md               ← global agent config and routing rules
    skills/
      forgemaster-orchestrator.md
      forgemaster-parallel-lanes.md
      forgemaster-writing-plans.md
      forgemaster-implementation.md
      forgemaster-systematic-debugging.md
      forgemaster-verification.md
      forgemaster-git-workflow.md
      forgemaster-code-review.md
      forgemaster-nova-session-handoff.md
  tools/
    chatgpt_to_nova.py      ← ChatGPT export migration script
  shard_index.json          ← auto-generated (do not commit)
  shard_graph.json          ← auto-generated (do not commit)
  nova_usage.jsonl          ← auto-generated (do not commit)
  .env                      ← your secrets (do not commit)
  .env.example              ← template (committed)
  .gitignore
  README.md
```

---

## NOVA MCP Tools (v2)

| Tool | Description |
|---|---|
| `nova_shard_interact` | Load shards into context by semantic relevance |
| `nova_shard_create` | Create new shard with post-write enrichment |
| `nova_shard_update` | Append to shard with auto-compaction |
| `nova_shard_search` | Search with confidence weighting |
| `nova_shard_list` | List all shards with confidence scores |
| `nova_shard_get` | Read full shard content, no side effects |
| `nova_shard_merge` | Merge shards into meta-shard |
| `nova_shard_archive` | Soft-archive (excludes from search) |
| `nova_shard_forget` | Hard delete with provenance log |
| `nova_shard_consolidate` | Run decay + compact + merge suggestion cycle |
| `nova_graph_query` | Query inter-shard knowledge graph |
| `nova_graph_relate` | Add directed relation between shards |
| `nova_session_flush` | Persist active sprint session to disk |
| `nova_session_load` | Restore stored session to memory |
| `nova_session_list` | List all stored session IDs |
| `nova_forgemaster_sprint` | Full 4-turn sprint pipeline |

---

## Notes

- Never manually edit files in `shards/` — always go through the MCP tools
- `shard_index.json`, `shard_graph.json`, and `nova_usage.jsonl` are auto-generated and should not be committed
- `.env` contains API keys — never commit it
- `nova_server.py` and `SKILL.md` in `_deprecated/` are kept as v1 reference — do not delete