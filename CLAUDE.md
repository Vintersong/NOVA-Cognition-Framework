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

    # Epistemic provenance
    provenance.py            ← chain-of-custody records (source_type, validator, mechanism, confidence_delta, supersession) backing nova_shard_validate

    # Maintenance & lifecycle
    maintenance.py           ← confidence decay, compaction, merge
    nott.py                  ← NOTT daemon: decay, compact, merge, graph sync

    # Retrieval & ranking
    ravens.py                ← HUGINN (Haiku fast retrieval) + MUNINN (Sonnet deep rerank)
    recall.py                ← recall utilities (complements ravens.py)
    nova_embeddings_local.py ← local all-MiniLM-L6-v2 embeddings
    clustering.py            ← shard clustering utilities
    arrow_cache.py           ← Arrow-format embedding cache
    spreading_activation.py  ← graph-based score propagation, MUNINN third pass
    embedding_integrity.py   ← HMAC-SHA256 signing/verification of shard embeddings

    # Permissions, gating & audit
    permissions.py           ← env-driven tool allow/deny (NOVA_DENIED_TOOLS / NOVA_DENIED_PREFIXES)
    capability_gate.py       ← runtime capability gating (imported by nova_server.py)
    audit_log.py             ← per-tool audit trail (imported by nova_server.py)
    access_log.py            ← shard access logging

    # Tool registry & handler modules (split from the nova_server.py god-object)
    tool_registry.py         ← canonical ToolSpec registry; @nova_tool validates names at import
    server_context.py        ← process-scoped ServerContext singleton; bootstrap() wires components + hook bus
    shard_tools.py           ← shard CRUD/lifecycle handlers (interact, create, update, validate, search, …)
    graph_tools.py           ← nova_graph_query / nova_graph_relate handlers
    session_tools.py         ← nova_session_flush / load / list handlers
    forgemaster_tools.py     ← nova_forgemaster_sprint / nova_cache_prewarm handlers
    gate_helpers.py          ← permission_error / gate_check / log_executed plumbing shared across handler modules
    reject.py                ← typed reject envelope (RejectCode enum + reject_payload())

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
    external_retrieval.py    ← nova_external_retrieval multi-agent deliberation pipeline
    calibrate.py             ← nova_calibrate_routing
    adversarial.py           ← adversarial shard contradiction testing — wired into NÓTT quarantine

    # Experimental
    ternary_net.py           ← ternary epistemic memory encoder (not yet wired into retrieval)

    # Tests
    test_nova.py             ← integration smoke tests
    test_adversarial.py      ← adversarial module tests
    test_clustering.py       ← clustering tests
    test_recall.py           ← recall pipeline tests
    test_quarantine.py       ← quarantine/isolation tests
    test_state_gating.py     ← capability gate tests
    test_provenance.py       ← provenance record + nova_shard_validate tests
    test_shard_concurrency.py ← lock-safe read-modify-write / CAS tests

    Gemini/
      gemini_mcp.py          ← Gemini Flash tools registered into nova_server
      output_event_bus.lua   ← Lua event bus for Gemini output routing

  tests/                     ← main pytest suite (run from repo root)
  utilities/
    chatgpt_to_nova.py       ← ChatGPT export migration
    claude_to_nova.py        ← Claude (Anthropic) conversation export → shards
    gemini_to_nova.py        ← Gemini MyActivity (Google Takeout) → shards
    grok_to_nova.py          ← Grok (xAI) export → shards
    lechat_to_nova.py        ← Le Chat (Mistral) export → shards
    perplexity_to_nova.py    ← Perplexity exported threads → shards
    docs_to_nova.py          ← standalone documents (design docs, papers) → reference shards
    shard_index.py           ← rebuild shard index manually
    dedup_json.py            ← duplicate shard detection
    autoresearch.py          ← automated research loop
    shard_compact.py         ← manual compaction helper
    theme_analyzer.py        ← theme distribution analysis
    backfill_source_summary.py ← backfill source_summary field on shards
    backfill_provenance.py   ← seed epistemic_provenance records on pre-existing shards
    backfill_graph_entities.py ← register legacy/imported shards as graph entities
    bench_report.py          ← benchmark report generation
    build_nova_shard_db.py   ← one-shot SQLite shard DB builder
    check_tool_docs.py       ← verify tool docstrings against schema
    convert_shards_to_md.py  ← export shards as plain markdown
    export_obsidian.py       ← standalone Obsidian export script
    export_ternary_dataset.py ← export training data for ternary_net
    train_ternary_net.py     ← train the ternary epistemic encoder
    usage_rollup.py          ← aggregate nova_usage.jsonl into summaries
    test_shards.py           ← shard integrity test suite

  shards/                    ← live shard data — never modify directly
  wiki/                      ← curated markdown pages (YAML frontmatter + [[wikilinks]])
  intake/                    ← drop zone for nidhogg_scan
  facts/                     ← curated .shard files for the SQLite facts pre-filter
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

## NOVA MCP Tools (39 total)

### Core shard ops (`nova_server.py`, 23)

| Tool | Purpose |
|---|---|
| `nova_shard_interact` | Load shards into context — start every session with this |
| `nova_shard_create` | Create new shard with guiding question |
| `nova_shard_update` | Append conversation turn to existing shard |
| `nova_shard_validate` | Record an epistemic validation event (source_type, validator, mechanism, confidence delta, supersession) on a shard's provenance record |
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
| `nova_cache_prewarm` | Pre-warm Anthropic prompt cache with top-N shard context; returns the cached `system` string for subsequent calls |

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

### External retrieval (`external_retrieval.py`, 1)

| Tool | Purpose |
|---|---|
| `nova_external_retrieval` | Fire Haiku + Sonnet/Opus deliberation over external sources; writes findings back as shards (reversible via archive/forget) |

### Calibrate (`calibrate.py`, 1)

| Tool | Purpose |
|---|---|
| `nova_calibrate_routing` | Analyse HUGINN consistency and Forgemaster sprint pass rates to suggest routing threshold adjustments |

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

**Default: do not write a handoff.** A handoff is information you cannot
recover from `git log`, the PR body, the codebase, or open issues. Most
sessions produce no such information.

Write a handoff **only** when one or more applies:

1. **Suspended work** — the session ends mid-task with state that is not
   committed and not obvious from the working tree. Record: branch, exact
   resume point, what was tried and rejected.
2. **Architectural decision** — a non-trivial choice was made whose
   *reasoning* isn't in a commit message, PR description, or ADR. Record:
   the choice, the alternatives considered, why the others lost. (If it's
   worth writing here, ask whether it should also be an ADR.)
3. **Discovered constraint** — something true about the system, a
   dependency, an external stakeholder, or production state that the next
   session would otherwise have to rediscover.
4. **Cross-session continuity** — work the user has explicitly said will
   resume in another session, with context that won't be in their prompt.

**Skip the handoff** when the session was:
- A code change that landed in a commit or PR (the commit message IS the handoff)
- A bug fix where the fix and its cause are visible in the diff
- A research/explanation task with no resulting artifact
- A configuration tweak, dep bump, or doc edit

**Where to write:**
- Update the most specific project shard, not `nova_infrastructure_handoff`.
  Search first; create a new shard only if no relevant one exists.
- One shard per *project*, not per session. Multiple sessions append.

**What to write:**
- One field minimum, four fields maximum, from: CURRENT STATE,
  IN PROGRESS, DECISIONS MADE, NEXT ACTION.
- Skip any field that's empty or git-recoverable. A handoff with just
  DECISIONS MADE is fine. A handoff with all four fields padded is bad.
- Cap each field at ~5 lines. If you need more, you're writing prose
  that belongs in an ADR or PR description.

**Self-test before writing:**
*"If I read `git log dev..HEAD` and the PR body next session, would I
still need this shard?"* If no, don't write it.

Next session starts with `nova_shard_interact(message="[project name] current state")`.

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
