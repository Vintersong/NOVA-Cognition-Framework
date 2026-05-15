# NOVA + Forgemaster — Claude Code Instructions

**At the start of every session: read `mcp/SKILL.md`, then call `nova_shard_interact` to load project context before doing anything else.**
**If `nova_shard_interact` returns no shards, this is a fresh install — read `mcp/ONBOARDING.md` and run the onboarding flow before proceeding.**

NOVA is a persistent memory MCP server that stores conversations as JSON shards with confidence decay, auto-compaction, and a knowledge graph. Forgemaster is the orchestration layer on top — it routes tasks to specialized LLM lanes using NOVA as shared context across sessions. They are one system.

---

## Directory Structure

```
NOVA-Whitepaper/
  mcp/
    # Core server
    nova_server.py           ← ACTIVE MCP server (registers all tool modules)
    config.py                ← all env vars and constants (single source of truth)
    schemas.py               ← Pydantic input models
    models.py                ← shared dataclasses (e.g. UsageSummary)
    requirements.txt         ← Python dependencies
    SKILL.md                 ← NOVA skill instructions
    ONBOARDING.md            ← fresh-install flow (triggered when no shards exist)

    # Shard I/O & persistence
    store.py                 ← shard JSON read/write and index management
    nova_shard_db.py         ← SQLite-backed shard index (nova_shard_index.db)
    shard_format.py          ← shard serialisation helpers
    shard_parser.py          ← shard parsing (used by facts.py)
    atomic_io.py             ← atomic file write primitives

    # Knowledge graph
    graph.py                 ← inter-shard knowledge graph ops

    # Maintenance & lifecycle
    maintenance.py           ← confidence decay, compaction, merge
    nott.py                  ← NOTT daemon: decay, compact, merge, graph sync

    # Retrieval & ranking
    ravens.py                ← HUGINN (Haiku fast retrieval) + MUNINN (Sonnet deep rerank)
    recall.py                ← recall utilities (complements ravens.py)
    nova_embeddings_local.py ← local all-MiniLM-L6-v2 embeddings
    clustering.py            ← shard clustering utilities
    arrow_cache.py           ← Arrow-format embedding cache

    # Permissions, gating & audit
    permissions.py           ← env-driven tool gating (_ALL_TOOL_NAMES whitelist)
    capability_gate.py       ← runtime capability gating (imported by nova_server.py)
    audit_log.py             ← per-tool audit trail (imported by nova_server.py)
    access_log.py            ← shard access logging

    # Session & sprint
    session_store.py         ← session persistence
    forgemaster_runtime.py   ← sprint orchestration
    usage.py                 ← JSONL operation logging
    timeutils.py             ← shared time/date helpers

    # Hook system (event-driven, registered via hooks.py)
    hooks.py                 ← hook registry (session/tool events)
    nova_hook_extract.py     ← hook: entity extraction on shard write
    nova_hook_precompact.py  ← hook: pre-compaction preparation
    nova_hook_recall.py      ← hook: post-recall side effects
    nova_hook_stop.py        ← hook: session-stop cleanup

    # Skill system
    skill_manifest.py        ← skill manifest parsing (imported by nova_server.py)
    skill_verification.py    ← skill schema verification

    # MCP tool modules
    evolve.py                ← nova_evolve tool (self-improvement loop)
    nidhogg.py               ← nidhogg_ingest/scan/status tools
    facts.py                 ← nova_facts_search / nova_facts_rebuild tools
    wiki.py                  ← wiki storage backend
    wiki_ingest.py           ← wiki ingestion pipeline
    wiki_tools.py            ← nova_wiki_* MCP tools
    obsidian_export.py       ← Obsidian vault export logic
    build_summary_index.py   ← summary index builder

    # Experimental
    ternary_net.py           ← ternary epistemic memory encoder (2026-05-14)
    adversarial.py           ← adversarial shard testing module

    # Tests
    test_nova.py             ← integration smoke tests
    test_adversarial.py      ← adversarial module tests
    test_clustering.py       ← clustering tests
    test_recall.py           ← recall pipeline tests
    test_quarantine.py       ← quarantine/isolation tests
    test_state_gating.py     ← capability gate tests

    Gemini/
      gemini_mcp.py          ← Gemini Flash tools registered into nova_server
      output_event_bus.lua   ← Lua event bus for Gemini output routing

  utilities/
    chatgpt_to_nova.py       ← ChatGPT export migration
    shard_index.py           ← rebuild shard index manually
    dedup_json.py            ← duplicate shard detection
    autoresearch.py          ← automated research loop
    shard_compact.py         ← manual compaction helper
    theme_analyzer.py        ← theme distribution analysis
    backfill_source_summary.py ← backfill source_summary field on shards
    build_nova_shard_db.py   ← one-shot SQLite shard DB builder
    check_tool_docs.py       ← verify tool docstrings against schema
    convert_shards_to_md.py  ← export shards as plain markdown
    export_obsidian.py       ← standalone Obsidian export script
    export_ternary_dataset.py ← export training data for ternary_net
    train_ternary_net.py     ← train the ternary epistemic encoder
    usage_rollup.py          ← aggregate nova_usage.jsonl into summaries
    test_shards.py           ← shard integrity test suite

  shards/                    ← live shard data — never modify directly
  nova_sessions/             ← flushed MCP session state
  output/                    ← built artifacts (games, experiments)
  forgemaster/
    AGENTS.md                ← orchestration config and model routing
    SKILL_LIBRARY.md         ← index of all skills across 15 domains
    STANDARDS.md             ← authoring standard for all forgemaster content
    skills/                  ← core orchestration skills (12 files)
    library/                 ← domain skill library (324 files, 25 categories)
    agents/                  ← agent persona definitions (221 personas + 99 reference files, 18 divisions)
  docs/                      ← reference and roadmap documents
  Donors/                    ← reference implementations (hermes-agent, OpenHarness)
  .env                       ← API keys (never commit)
```

---

## NOVA MCP Tools (35 total)

### Core shard ops (`nova_server.py`, 21)

| Tool | Purpose |
|---|---|
| `nova_shard_interact` | Load shards into context — start every session with this |
| `nova_shard_create` | Create new shard with guiding question |
| `nova_shard_update` | Append conversation turn to existing shard |
| `nova_shard_search` | Search by keyword with confidence weighting |
| `nova_shard_query_state` | Inspect computed shard state (confidence, tags, decay) without loading body |
| `nova_obsidian_export` | Export shard set as Obsidian-compatible markdown vault |
| `nova_shard_index` | Rebuild or inspect the shard index |
| `nova_shard_summary` | Summarise shard contents |
| `nova_shard_list` | List all shards sorted by confidence |
| `nova_shard_get` | Read full shard content, no side effects |
| `nova_shard_get_full` | Cold-path full-body fetch — returns summary + conversation body |
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

### Wiki (`wiki_tools.py`, 6)

| Tool | Purpose |
|---|---|
| `nova_wiki_schema` | Inspect wiki page schema |
| `nova_wiki_ingest` | Ingest markdown/source into the wiki |
| `nova_wiki_query` | Search across wiki pages |
| `nova_wiki_get` | Read a single wiki page in full |
| `nova_wiki_list` | List wiki pages |
| `nova_wiki_lint` | Lint wiki content for schema compliance |

### Nidhogg — repo scanner (`nidhogg.py`, 3)

| Tool | Purpose |
|---|---|
| `nidhogg_ingest` | Pull repo contents into the scanner index |
| `nidhogg_scan` | Scan for issues/patterns |
| `nidhogg_status` | Report scanner state |

### Evolution (`evolve.py`, 1)

| Tool | Purpose |
|---|---|
| `nova_evolve` | Self-improvement loop over shards/prompts |

### Facts (`facts.py`, 2)

| Tool | Purpose |
|---|---|
| `nova_facts_search` | Search the SQLite-backed `.shard` facts corpus |
| `nova_facts_rebuild` | Rebuild the facts index from shard sources |

### Gemini (`Gemini/gemini_mcp.py`, 2)

| Tool | Purpose |
|---|---|
| `gemini_execute_ticket` | Run an implementation ticket on Gemini Flash |
| `gemini_load_file` | Load a file into the Gemini worker's context |

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
| `forgemaster-heavyskill` | Hard verifiable reasoning (math, algorithmic, multi-constraint) — K=3 Haiku thinkers + Sonnet deliberation |
| `forgemaster-emotional-state-routing` | Routing hook: escalates tickets when session arousal is high + confidence is low (desperation guard) |

For all other domains see `forgemaster/SKILL_LIBRARY.md` (25 categories, 324 skills).

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
| `CLAUDE_API_KEY` | — | Powers HUGINN + MUNINN retrieval |
| `HUGINN_MODEL` | `claude-haiku-4-5-20251001` | Fast retrieval pass |
| `MUNINN_MODEL` | `claude-sonnet-4-6` | Deep rerank pass |
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
- If `CLAUDE_API_KEY` is absent, HUGINN and MUNINN fall back to local embeddings silently
