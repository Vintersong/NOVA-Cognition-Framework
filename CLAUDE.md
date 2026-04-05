# NOVA + Forgemaster — Claude Code Instructions

## What This Repo Is

This repository contains two interconnected systems:

**NOVA** — Persistent memory MCP server. Stores conversations as modular JSON shards with confidence decay, auto-compaction, knowledge graph, and semantic retrieval. 16 tools exposed via Model Context Protocol.

**Forgemaster** — Multi-agent orchestration layer. Routes tasks to specialized LLM lanes, uses NOVA as persistent memory across sessions. Skill library defines agent behavior.

NOVA is the memory layer. Forgemaster is the orchestration layer. They are one system.

---

## Directory Structure

```
NOVA-Cognition-Framework/
  mcp/
    nova_server.py           ← ACTIVE MCP server — always use this one
    config.py                ← env vars: paths, thresholds, model names
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
    SKILL.md                 ← ACTIVE skill instructions
    requirements.txt
    Gemini/
      gemini_mcp.py          ← Gemini tools registered into nova_server
  utilities/
    shard_index.py           ← index manager (standalone rebuild)
    dedup_json.py            ← duplicate shard detection
    chatgpt_to_nova.py       ← ChatGPT export migration
    autoresearch.py          ← automated research utility
    shard_compact.py         ← manual compaction helper
    theme_analyzer.py        ← theme distribution analysis
    test_shards.py           ← shard I/O tests
  shards/                    ← live shard data, never modify directly
  nova_sessions/             ← flushed MCP session state
  output/                    ← built artifacts (games, experiments)
  forgemaster/
    AGENTS.md                ← global agent configuration
    SKILL_LIBRARY.md         ← index of all skills across 15 domains
    skills/                  ← core orchestration skills
    library/                 ← domain skill library
    agents/                  ← agent persona definitions
  docs/
    ROADMAP.md               ← project roadmap
    REFACTOR_ROADMAP.md      ← refactor plan
    nova-support-papers/     ← PDFs and pitch deck (not committed)
  Donors/                    ← reference implementations (hermes-agent, OpenHarness)
  .env                       ← API keys (never commit)
  shard_index.json           ← auto-generated (never commit)
  shard_graph.json           ← auto-generated (never commit)
  nova_usage.jsonl           ← auto-generated (never commit)
```

---

## Setup

```bash
cd mcp
pip install -r requirements.txt

# Edit root .env — set CLAUDE_API_KEY and GEMINI_API_KEY
# Optionally override the shard directory (defaults to repo root /shards)
# export NOVA_SHARD_DIR="C:/Users/Moldo/Master Project NOVA/repos/forgemaster-harvest/NOVA-Cognition-Framework/shards"

# Run the server
python nova_server.py
```

**Root `.env`** (repo root — loaded by nova_server via config.py):
```
CLAUDE_API_KEY=sk-ant-...          # Powers HUGINN (Haiku) + MUNINN (Sonnet)
GEMINI_API_KEY=...                 # Powers gemini_worker / gemini_mcp
GEMINI_MODEL=gemini-2.5-flash
CONFIDENCE_THRESHOLD=0.65
```

**Claude Desktop config** (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "nova": {
      "command": "python",
      "args": ["C:/Users/Moldo/Master Project NOVA/repos/forgemaster-harvest/NOVA-Cognition-Framework/mcp/nova_server.py"],
      "env": {
        "NOVA_SHARD_DIR": "C:/Users/Moldo/Master Project NOVA/repos/forgemaster-harvest/NOVA-Cognition-Framework/shards",
        "CLAUDE_API_KEY": "sk-ant-...",
        "GEMINI_API_KEY": "..."
      }
    }
  }
}
```

---

## NOVA v2 Tools Reference

| Tool | Purpose |
|---|---|
| `nova_shard_interact` | Load shards into context — start every session with this |
| `nova_shard_create` | Create new shard with guiding question |
| `nova_shard_update` | Append conversation turn to existing shard |
| `nova_shard_search` | Search by keyword with confidence weighting |
| `nova_shard_list` | List all shards sorted by confidence |
| `nova_shard_get` | Read full shard content, no side effects |
| `nova_shard_merge` | Merge related shards into meta-shard |
| `nova_shard_archive` | Soft-archive stale shards |
| `nova_shard_forget` | Hard exclude with provenance log |
| `nova_shard_consolidate` | Run full maintenance: decay + compact + merge suggestions |
| `nova_graph_query` | Query inter-shard knowledge graph |
| `nova_graph_relate` | Manually add directed relation between shards |
| `nova_session_flush` | Persist active sprint session to disk |
| `nova_session_load` | Restore stored session to memory |
| `nova_session_list` | List all stored session IDs |
| `nova_forgemaster_sprint` | Full 4-turn sprint pipeline |

---

## Forgemaster Skill Library

Load the relevant skill file before each operation type.
All skills live in `forgemaster/skills/`.

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

## Architecture Rules

**Never modify files in `shards/` directly.** Use the MCP tools.

**Never commit `.env`, `shard_index.json`, `shard_graph.json`, `nova_usage.jsonl`.** These are runtime files.

**Always use `nova_server.py`.** The v1 server (`mcp/_deprecated/`) is reference only.

**Confidence scores matter.** Shards with confidence < 0.4 are tagged `low_confidence` and excluded from default search. They still exist — use `include_low_confidence=True` to recall them deliberately.

**The knowledge graph is the navigation layer.** After creating related shards, wire them with `nova_graph_relate`. Before starting work on something that depends on prior decisions, query the graph: `nova_graph_query(target=shard_id, relation_type=depends_on)`.

---

## Key Environment Variables

| Variable | Default | Description |
|---|---|---|
| `NOVA_SHARD_DIR` | `shards` | Path to shard JSON files |
| `CLAUDE_API_KEY` | none | Anthropic key — powers HUGINN (Haiku) + MUNINN (Sonnet) retrieval |
| `HUGINN_MODEL` | `claude-haiku-3-5` | Model used by HUGINN fast-retrieval pass |
| `MUNINN_MODEL` | `claude-sonnet-4-5` | Model used by MUNINN deep-rerank pass |
| `HUGINN_CONFIDENCE_THRESHOLD` | `0.7` | If HUGINN max score ≥ this, MUNINN is skipped |
| `GEMINI_API_KEY` | none | Required by gemini_worker.py and gemini_mcp.py |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model for implementation tickets |
| `CONFIDENCE_THRESHOLD` | `0.65` | Below this, Gemini tickets escalate to Sonnet |
| `NOVA_COMPACT_THRESHOLD` | `30` | Turns before auto-compaction |
| `NOVA_DECAY_RATE` | `0.05` | Confidence decay per 7-day period |
| `NOVA_MERGE_THRESHOLD` | `0.85` | Cosine similarity for merge suggestions |
| `NOVA_CONFIDENCE_LOW` | `0.4` | Below this, shard tagged `low_confidence` |
| `NOVA_RECENT_DAYS` | `3` | Accessed within N days → tagged `recent` |
| `NOVA_STALE_DAYS` | `14` | Not accessed for N days → tagged `stale` |

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

# Rebuild index manually
cd utilities && python shard_index.py
```

---

## What Not To Do

- Do not edit shard JSON files by hand
- Do not use deprecated scripts — the v1 server and OpenAI-based enrichment have been removed
- Do not skip `nova_shard_consolidate` indefinitely — run it every 3 sprints
- Do not start implementation without loading NOVA context first
- Do not end a session without writing the handoff to NOVA
- Do not commit the shards directory — it is personal data
- Do not use OpenAI models — use claude-haiku for research/docs, gemini-flash for implementation
- HUGINN and MUNINN are live — ensure `CLAUDE_API_KEY` is set in `.env` for LLM-powered retrieval
- If `CLAUDE_API_KEY` is absent, HUGINN and MUNINN silently fall back to local embeddings/token-overlap
