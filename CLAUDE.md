# NOVA + Forgemaster — Claude Code Instructions

**At the start of every session: read `mcp/SKILL.md`, then call `nova_shard_interact` to load project context before doing anything else.**
**If `nova_shard_interact` returns no shards, this is a fresh install — read `mcp/ONBOARDING.md` and run the onboarding flow before proceeding.**

NOVA is a persistent memory MCP server that stores conversations as JSON shards with confidence decay, auto-compaction, and a knowledge graph. Forgemaster is the orchestration layer on top — it routes tasks to specialized LLM lanes using NOVA as shared context across sessions. They are one system.

---

## Directory Structure

```
NOVA-Cognition-Framework/
  mcp/
    nova_server.py           ← ACTIVE MCP server
    config.py                ← all env vars and constants (single source of truth)
    schemas.py               ← Pydantic input models
    store.py                 ← shard I/O and index management
    graph.py                 ← knowledge graph ops
    maintenance.py           ← confidence decay, compaction, merge
    permissions.py           ← env-driven tool gating
    session_store.py         ← session persistence
    forgemaster_runtime.py   ← sprint orchestration
    ravens.py                ← HUGINN (Haiku fast retrieval) + MUNINN (Sonnet deep rerank)
    nott.py                  ← NOTT daemon: decay, compact, merge, graph sync
    nova_embeddings_local.py ← local all-MiniLM-L6-v2 embeddings
    gemini_worker.py         ← standalone Gemini Flash MCP server
    SKILL.md                 ← NOVA skill instructions
    Gemini/
      gemini_mcp.py          ← Gemini tools registered into nova_server
  utilities/
    chatgpt_to_nova.py       ← ChatGPT export migration
    shard_index.py           ← rebuild shard index manually
    dedup_json.py            ← duplicate shard detection
    autoresearch.py          ← automated research loop
    shard_compact.py         ← manual compaction helper
    theme_analyzer.py        ← theme distribution analysis
  shards/                    ← live shard data — never modify directly
  nova_sessions/             ← flushed MCP session state
  output/                    ← built artifacts (games, experiments)
  forgemaster/
    AGENTS.md                ← orchestration config and model routing
    SKILL_LIBRARY.md         ← index of all skills across 15 domains
    STANDARDS.md             ← authoring standard for all forgemaster content
    skills/                  ← core orchestration skills (10 files)
    library/                 ← domain skill library (208 files, 15 categories)
    agents/                  ← agent persona definitions (326 files, 18 divisions)
  docs/                      ← reference and roadmap documents
  Donors/                    ← reference implementations (hermes-agent, OpenHarness)
  .env                       ← API keys (never commit)
```

---

## NOVA MCP Tools (18)

| Tool | Purpose |
|---|---|
| `nova_shard_interact` | Load shards into context — start every session with this |
| `nova_shard_create` | Create new shard with guiding question |
| `nova_shard_update` | Append conversation turn to existing shard |
| `nova_shard_search` | Search by keyword with confidence weighting |
| `nova_shard_index` | Rebuild or inspect the shard index |
| `nova_shard_summary` | Summarise shard contents |
| `nova_shard_list` | List all shards sorted by confidence |
| `nova_shard_get` | Read full shard content, no side effects |
| `nova_shard_merge` | Merge related shards into meta-shard |
| `nova_shard_archive` | Soft-archive stale shards |
| `nova_shard_forget` | Hard exclude with provenance log |
| `nova_shard_consolidate` | Full maintenance: decay + compact + merge suggestions |
| `nova_graph_query` | Query inter-shard knowledge graph |
| `nova_graph_relate` | Add directed relation between shards |
| `nova_session_flush` | Persist active sprint session to disk |
| `nova_session_load` | Restore stored session to memory |
| `nova_session_list` | List all stored session IDs |
| `nova_forgemaster_sprint` | Full 4-turn sprint pipeline |

Gemini tools (via `mcp/Gemini/gemini_mcp.py`): `gemini_execute_ticket`, `gemini_load_file`

---

## Forgemaster Core Skills

All in `forgemaster/skills/`. Load the relevant one before each operation.

| Skill | When to use |
|---|---|
| `forgemaster-orchestrator` | Sprint start, task routing |
| `forgemaster-parallel-lanes` | Dispatching 2+ independent tickets |
| `forgemaster-writing-plans` | Decomposing design doc into tickets |
| `forgemaster-implementation` | Single ticket execution |
| `forgemaster-systematic-debugging` | Root cause investigation |
| `forgemaster-verification` | Before claiming any work is complete |
| `forgemaster-git-workflow` | Branch setup, integration, PR creation |
| `forgemaster-code-review` | Two-stage spec + quality review |
| `forgemaster-qa-review` | Stage 3 structural QA |
| `forgemaster-nova-session-handoff` | Persisting state across sessions |

For all other domains see `forgemaster/SKILL_LIBRARY.md` (15 categories, 208 skills).

---

## Standard Sprint Workflow

```
1. nova_shard_interact(message="[project name] current state")
2. Read design doc → forgemaster-writing-plans
3. Classify tickets → forgemaster-orchestrator
4. Dispatch lanes → forgemaster-parallel-lanes
5. Review results → forgemaster-code-review
6. nova_shard_update(shard_id=...) — write decisions made
7. Every 3 sprints: nova_shard_consolidate()
```

---

## Session Handoff Protocol

Before ending any session, write to NOVA:

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

Next session starts with `nova_shard_interact(message="[project name] current state")`.
This is not optional. Without this, every session starts from zero.

---

## Architecture Rules

- Never modify `shards/` directly — use MCP tools only
- Never commit `.env`, `shard_index.json`, `shard_graph.json`, `nova_usage.jsonl`
- Always use `nova_server.py` — no deprecated servers remain
- Confidence < 0.4 → shard tagged `low_confidence`, excluded from default search. Use `include_low_confidence=True` to recall deliberately
- After creating related shards, wire them with `nova_graph_relate`. Before dependent work, query: `nova_graph_query(target=shard_id, relation_type=depends_on)`

---

## Key Environment Variables

| Variable | Default | Notes |
|---|---|---|
| `NOVA_SHARD_DIR` | `shards` | Path to shard JSON files |
| `ANTHROPIC_API_KEY` | — | Powers HUGINN + MUNINN retrieval |
| `HUGINN_MODEL` | `claude-haiku-3-5` | Fast retrieval pass |
| `MUNINN_MODEL` | `claude-sonnet-4-5` | Deep rerank pass |
| `HUGINN_CONFIDENCE_THRESHOLD` | `0.7` | Score >= this skips MUNINN |
| `GEMINI_API_KEY` | — | Required for Gemini worker |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Implementation lane model |
| `CONFIDENCE_THRESHOLD` | `0.65` | Below this, Gemini escalates to Sonnet |
| `NOVA_COMPACT_THRESHOLD` | `30` | Turns before auto-compaction |
| `NOVA_DECAY_RATE` | `0.05` | Confidence decay per 7-day period |
| `NOVA_MERGE_THRESHOLD` | `0.85` | Cosine similarity floor for merge suggestions |
| `NOVA_CONFIDENCE_LOW` | `0.4` | Below this → `low_confidence` tag |
| `NOVA_RECENT_DAYS` | `3` | Within N days → `recent` tag |
| `NOVA_STALE_DAYS` | `14` | Not accessed N days → `stale` tag |

---

## What Not To Do

- Do not edit shard JSON files by hand
- Do not skip `nova_shard_consolidate` — run every 3 sprints
- Do not start implementation without loading NOVA context first
- Do not end a session without the handoff write
- Do not commit the shards directory — personal data
- Do not use OpenAI models — Haiku for research/docs, Gemini Flash for implementation, Sonnet for architecture/review
- If `ANTHROPIC_API_KEY` is absent, HUGINN and MUNINN fall back to local embeddings silently
