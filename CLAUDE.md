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
    nova_server.py        ← ACTIVE MCP server — always use this one
    SKILL_v2.md           ← ACTIVE skill instructions
    _deprecated/          ← v1 reference only, do not modify
    requirements.txt
  python/
    shard_index.py        ← index manager, imported by nova_server
    context_extractor.py  ← batch enrichment utility
  shards/                 ← live shard data, never modify directly
  forgemaster/
    AGENTS.md             ← global agent configuration
    skills/               ← skill library markdown files
  tools/
    chatgpt_to_nova.py    ← migration utility
  .env                    ← OpenAI key (never commit)
  shard_index.json        ← auto-generated (never commit)
  shard_graph.json        ← auto-generated (never commit)
  nova_usage.jsonl        ← auto-generated (never commit)
```

---

## Setup

```bash
cd mcp
pip install -r requirements.txt

# Copy env template (no API key required)
cp ../.env.example ../.env

# Optionally override the shard directory (defaults to repo root /shards)
# export NOVA_SHARD_DIR="C:/Users/Moldo/Master Project NOVA/repos/forgemaster-harvest/NOVA-Cognition-Framework/shards"

# Run the server
python nova_server.py
```

**Claude Desktop config** (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "nova": {
      "command": "python",
      "args": ["C:/Users/Moldo/Master Project NOVA/repos/forgemaster-harvest/NOVA-Cognition-Framework/mcp/nova_server.py"],
      "env": {
        "NOVA_SHARD_DIR": "C:/Users/Moldo/Master Project NOVA/repos/forgemaster-harvest/NOVA-Cognition-Framework/shards"
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
| `OPENAI_API_KEY` | none | Not required — local embeddings used instead (legacy field) |
| `NOVA_COMPACT_THRESHOLD` | `30` | Turns before auto-compaction |
| `NOVA_DECAY_RATE` | `0.05` | Confidence decay per 7-day period |
| `NOVA_MERGE_THRESHOLD` | `0.85` | Cosine similarity for merge suggestions |

---

## Common Commands

```bash
# Run dry-run migration from ChatGPT export
python tools/chatgpt_to_nova.py --dry-run

# Run actual migration
python tools/chatgpt_to_nova.py

# Batch enrich shards with embeddings (run after migration)
cd python && python context_extractor.py

# Rebuild index manually
cd python && python shard_index.py
```

---

## What Not To Do

- Do not edit shard JSON files by hand
- Do not use the v1 server in `mcp/_deprecated/` — it is reference only
- Do not skip `nova_shard_consolidate` indefinitely — run it every 3 sprints
- Do not start implementation without loading NOVA context first
- Do not end a session without writing the handoff to NOVA
- Do not commit the shards directory — it is personal data
- Do not route any task to gpt-4o or any OpenAI model — use claude-haiku for research/docs instead
