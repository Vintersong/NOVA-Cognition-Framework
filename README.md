# NOVA-Cognition-Framework

A unified repository containing **NOVA** (persistent AI memory) and **Forgemaster** (multi-agent orchestration). NOVA is the memory layer. Forgemaster is the execution layer. They share one repo and one data store.

> **Architecture note:** `nova_server.py` is a thin MCP adapter. All business logic lives in dedicated modules: `config`, `schemas`, `store`, `graph`, `maintenance`, `usage`, `ravens`, `nott`, `hooks`. See [Module Architecture](#module-architecture) below.

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
  │       ├── implementation / boilerplate        → Gemini Flash (gemini_execute_ticket)
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

Copy the example file and fill in your keys:

```bash
cp .env.example .env
```

Required keys in `.env` (repo root — loaded by `nova_server.py` via `config.py`):

```
CLAUDE_API_KEY=sk-ant-...          # Powers HUGINN (Haiku) + MUNINN (Sonnet)
GEMINI_API_KEY=...                 # Powers gemini_worker / gemini_mcp
GEMINI_MODEL=gemini-2.5-flash
CONFIDENCE_THRESHOLD=0.65
```

> Use `gemini-2.5-flash` — `gemini-2.5-pro` exhausts the free tier immediately.

### 3. Register servers in Claude Desktop

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

### 4. Run the NOVA MCP server

```bash
python mcp/nova_server.py
```

---

## Directory Structure

```
NOVA-Cognition-Framework/
  mcp/
    nova_server.py           ← active MCP server — 16 NOVA tools + Gemini tools
    config.py                ← env vars: paths, thresholds, Norse Pantheon config
    schemas.py               ← Pydantic input models for all tool handlers
    store.py                 ← shard I/O and index management
    graph.py                 ← knowledge graph load/save/query/relate
    maintenance.py           ← confidence decay, compaction, cosine, merge
    usage.py                 ← operation logging to nova_usage.jsonl
    models.py                ← UsageSummary, ShardRecord, PermissionDenial
    permissions.py           ← ToolPermissionContext (env-driven tool gating)
    session_store.py         ← session persistence
    forgemaster_runtime.py   ← sprint orchestration
    ravens.py                ← HUGINN (Haiku fast retrieval) + MUNINN (Sonnet deep rerank)
    nott.py                  ← NÓTT daemon: decay · compact · merge · graph sync
    hooks.py                 ← NovaHookRegistry / NovaHookEvent
    nova_embeddings_local.py ← local all-MiniLM-L6-v2 embeddings + compaction
    gemini_worker.py         ← standalone Gemini Flash MCP server
    build_summary_index.py   ← batch-build summary_index.json
    test_nova.py             ← memory explorer / health check
    SKILL.md                 ← active agent cognitive instructions
    requirements.txt
    Gemini/
      gemini_mcp.py          ← Gemini tools registered into nova_server
  utilities/
    shard_index.py           ← index manager (standalone rebuild)
    dedup_json.py            ← duplicate shard detection
    chatgpt_to_nova.py       ← ChatGPT export → NOVA shard migration
    autoresearch.py          ← automated research utility
    shard_compact.py         ← manual compaction helper
    theme_analyzer.py        ← theme distribution analysis
    test_shards.py           ← shard I/O tests
  shards/                    ← live shard data (never edit directly)
  nova_sessions/             ← flushed MCP session state
  forgemaster/
    AGENTS.md                ← agent roles, routing rules, model assignments
    SKILL_LIBRARY.md         ← index of all skills across 15 domains
    STANDARDS.md             ← coding and output standards
    skills/                  ← core orchestration skills
    library/                 ← domain skill library (game-dev, engineering, etc.)
    agents/                  ← agent persona definitions
    rules/                   ← coding standards and hook rules
    templates/               ← reusable document templates
    workflows/               ← step-by-step workflow files
    slash-commands/          ← slash-command definitions
    docs/                    ← Forgemaster reference docs
  docs/                      ← project reference docs (roadmap, architecture)
  shard_index.json           ← auto-generated (do not commit)
  shard_graph.json           ← auto-generated (do not commit)
  nova_usage.jsonl           ← auto-generated (do not commit)
  .env                       ← secrets (do not commit)
  .env.example               ← template (committed)
  .gitignore
  CLAUDE.md                  ← Claude Code session instructions
  KNOWN_ISSUES.md
  README.md
```

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

`nova_server.py` is a thin MCP adapter. All business logic lives in dedicated modules:

| Module | Responsibility |
|---|---|
| `config.py` | Single source for all env vars and defaults |
| `schemas.py` | 17 Pydantic input models for tool handlers |
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
| `build_summary_index.py` | Batch-build `summary_index.json` from all shards |
| `models.py` | `UsageSummary`, `ShardRecord`, `PermissionDenial` data classes |

---

## Key Environment Variables

| Variable | Default | Description |
|---|---|---|
| `NOVA_SHARD_DIR` | `shards` | Path to shard JSON files |
| `CLAUDE_API_KEY` | none | Anthropic key — powers HUGINN (Haiku) + MUNINN (Sonnet) retrieval |
| `HUGINN_MODEL` | `claude-haiku-3-5` | Model used by HUGINN fast-retrieval pass |
| `MUNINN_MODEL` | `claude-sonnet-4-5` | Model used by MUNINN deep-rerank pass |
| `HUGINN_CONFIDENCE_THRESHOLD` | `0.7` | If HUGINN max score ≥ this, MUNINN is skipped |
| `GEMINI_API_KEY` | none | Required by `gemini_worker.py` and `gemini_mcp.py` |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model for implementation tickets |
| `CONFIDENCE_THRESHOLD` | `0.65` | Below this, Gemini tickets escalate to Sonnet |
| `NOTT_COUNT_THRESHOLD` | `100` | Operation count that triggers NÓTT maintenance cycle |
| `NOVA_COMPACT_THRESHOLD` | `30` | Turns before auto-compaction |
| `NOVA_DECAY_RATE` | `0.05` | Confidence decay per 7-day period |
| `NOVA_MERGE_THRESHOLD` | `0.85` | Cosine similarity for merge suggestions |
| `NOVA_CONFIDENCE_LOW` | `0.4` | Below this, shard tagged `low_confidence` |
| `NOVA_RECENT_DAYS` | `3` | Accessed within N days → tagged `recent` |
| `NOVA_STALE_DAYS` | `14` | Not accessed for N days → tagged `stale` |

> If `CLAUDE_API_KEY` is absent, HUGINN and MUNINN silently fall back to local embeddings/token-overlap.

---

## Forgemaster Skill Library

Load the relevant skill file before each operation type. All skills live in `forgemaster/skills/`.

| Skill | When to use |
|---|---|
| `forgemaster-orchestrator` | Starting a sprint, routing tickets |
| `forgemaster-parallel-lanes` | Dispatching 2+ independent tickets |
| `forgemaster-writing-plans` | Decomposing design doc into tickets |
| `forgemaster-implementation` | Executing a single ticket |
| `forgemaster-systematic-debugging` | Investigating any bug or failure |
| `forgemaster-verification` | Before claiming any work is complete |
| `forgemaster-git-workflow` | Branch setup, integration, PR creation |
| `forgemaster-code-review` | Two-stage review after implementation |
| `forgemaster-qa-review` | QA pass before sprint close |
| `forgemaster-nova-session-handoff` | Persisting state across session boundaries |

---

## Standard Sprint Workflow

```
1. nova_shard_interact(message="[project name] current state")
   → Load project context from NOVA

2. Read design doc or feature request
   → Use forgemaster-writing-plans skill

3. Classify each ticket by type
   → Use forgemaster-orchestrator skill

4. Dispatch lanes
   → Use forgemaster-parallel-lanes skill
   → Each lane gets: ticket + relevant skill + NOVA context

5. Review results
   → Use forgemaster-code-review skill (spec compliance first, then quality)

6. nova_shard_update(shard_id=[project shard], ...)
   → Write decisions made this sprint to NOVA

7. After every 3 sprints:
   nova_shard_consolidate()
   → Decay stale shards, compact bloated ones, surface merge candidates
```

---

## Session Handoff Protocol

When approaching context limit or ending a session, ALWAYS write to NOVA before stopping:

```python
nova_shard_update(
    shard_id="[project-shard-id]",
    user_message="Session handoff",
    ai_response="""
    CURRENT STATE: [branch, last completed ticket, test status]
    IN PROGRESS: [what was started, what remains]
    DECISIONS MADE: [key architectural choices and why]
    NEXT ACTION: [exactly what to do first next session]
    """
)
```

Next session starts with:
```python
nova_shard_interact(message="[project name] current state")
```

This is not optional. Without this, every session starts from zero.

---

## Common Commands

```bash
# Run dry-run migration from ChatGPT export
python utilities/chatgpt_to_nova.py --dry-run

# Run actual migration
python utilities/chatgpt_to_nova.py

# Batch enrich pending shards with local embeddings (run after migration)
cd mcp && python -c "
import os, json, sys
from pathlib import Path
sys.path.insert(0, '.')
SHARD_DIR = os.environ.get('NOVA_SHARD_DIR', '../shards')
from nova_embeddings_local import enrich_shard
for fpath in Path(SHARD_DIR).glob('*.json'):
    data = json.load(open(fpath, encoding='utf-8'))
    if data.get('meta_tags', {}).get('enrichment_status') != 'enriched_local':
        enrich_shard(data['shard_id'], data)
        json.dump(data, open(fpath, 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
"

# Rebuild shard index manually
cd utilities && python shard_index.py

# Rebuild summary index
cd mcp && python build_summary_index.py
```

---

## Notes

- Never manually edit files in `shards/` — always use the MCP tools
- `shard_index.json`, `shard_graph.json`, and `nova_usage.jsonl` are auto-generated — do not commit
- `.env` files contain API keys — never commit them
- Do not skip `nova_shard_consolidate` indefinitely — run it every 3 sprints
- Do not use OpenAI models — use `claude-haiku` for research/docs, `gemini-flash` for implementation