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
    gate_helpers.py          ← permission_reject / gate_check_model / log_executed plumbing shared across handler modules
    reject.py                ← typed reject envelope (RejectCode enum + RejectPayload model)
    outputs.py               ← Pydantic output models — every handler's return annotation, so tools publish a real outputSchema
    prompts.py               ← MCP prompts: 6 workflow openers + one per core forgemaster skill
    result_middleware.py     ← server middleware: sets isError on failing tool results
    approval.py              ← elicitation resolver — operator approval for destructive tools
    active_request.py        ← contextvar for the live MCP request

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

    # MCP tool modules
    evolve.py                ← nova_evolve tool (self-improvement loop)
    nidhogg.py               ← nidhogg_ingest/scan/status tools
    code_index.py            ← nova_code_search tool (AST-chunked semantic code search over mcp/)
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
    skills/                  ← core orchestration skills (14 files)
    library/                 ← domain skill library (324 files, 24 categories)
    agents/                  ← agent persona definitions (322 files, 18 divisions)
  docs/                      ← reference and roadmap documents
  .env                       ← API keys (never commit)
```

---

## NOVA MCP Tools (41 total)

Generated from the registry and the handlers' own docstrings — do not edit by
hand. Regenerate with:

```bash
python utilities/dump_tool_manifest.py --write-table CLAUDE.md
```

Handlers live in `shard_tools.py` (16), `graph_tools.py` (2), `session_tools.py`
(3), `forgemaster_tools.py` (2), `wiki_tools.py` (6), `facts.py` (2),
`nidhogg.py` (3), `evolve.py` (1), `Gemini/gemini_mcp.py` (2),
`external_retrieval.py` (1), `calibrate.py` (1), `huginn_tools.py` (1),
`code_index.py` (1). `nova_server.py` registers none of them; it is bootstrap
and wiring only.

<!-- BEGIN GENERATED TOOL TABLE -->
| Tool | Title | Purpose |
|---|---|---|
| `gemini_execute_ticket` | Execute Ticket via Gemini | Send a structured ticket to Gemini Flash for code generation. |
| `gemini_load_file` | Load File as Context | Load a file from disk to use as codebase context for ticket execution. _(read-only)_ |
| `nidhogg_ingest` | Ingest Document (Nidhogg) | Ingest a single document into NOVA's shard graph. _(**asks for approval**)_ |
| `nidhogg_scan` | Scan Intake Directory | Scan the intake/ directory and ingest all pending files. _(**asks for approval**)_ |
| `nidhogg_status` | Nidhogg Status | Show the Nidhogg ingestion manifest — what files have been ingested, which shards they matched, and which were flagged as merge candidates. _(read-only)_ |
| `nova_cache_prewarm` | Pre-warm Prompt Cache | Pre-warm the Anthropic prompt cache with a summary context built from the top-N highest-confidence shards. |
| `nova_calibrate_routing` | Analyse Routing Thresholds | Analyse HUGINN retrieval logs and Forgemaster sprint logs to calibrate routing thresholds and model routing based on empirical performance data. _(read-only)_ |
| `nova_code_search` | Search NOVA Source Code | Semantic search over NOVA's own mcp/ source code. _(read-only)_ |
| `nova_evolve` | Run Self-Improvement Loop | Run one NOVA self-evolution cycle. _(**asks for approval**)_ |
| `nova_external_retrieval` | External Retrieval Deliberation | External retrieval deliberation pipeline (v1.0). |
| `nova_facts_rebuild` | Rebuild Facts Index | Re-scan FACTS_DIR and rebuild the SQLite index. |
| `nova_facts_search` | Search Facts Corpus | Keyword search over the curated facts corpus (`.shard` files). _(read-only)_ |
| `nova_forgemaster_sprint` | Run Forgemaster Sprint | Run a full Forgemaster sprint: orchestrator → planner → implementer → reviewer. _(**asks for approval**)_ |
| `nova_graph_query` | Query Knowledge Graph | Query the inter-shard knowledge graph. _(read-only)_ |
| `nova_graph_relate` | Relate Two Shards | Manually add a directed relation between two shards in the knowledge graph. |
| `nova_huginn_candidates` | HUGINN Candidate Pre-filter | Pre-filter the shard index for a query and return a small candidate list ready to pass to a HUGINN agent prompt. _(read-only)_ |
| `nova_obsidian_export` | Export Shards to Obsidian | Export all shards to an Obsidian vault as Markdown files with YAML frontmatter and [[wikilink]] edges derived from the knowledge graph. |
| `nova_session_flush` | Flush Session to Disk | Persist an active session to disk and remove it from memory. |
| `nova_session_list` | List Stored Sessions | List all session IDs currently persisted on disk. _(read-only)_ |
| `nova_session_load` | Load Stored Session | Load a previously flushed session from disk into memory. |
| `nova_shard_archive` | Archive Shard | Soft-archive a shard. _(**asks for approval**)_ |
| `nova_shard_consolidate` | Run Maintenance Cycle | Trigger a full NÓTT maintenance cycle (fire-and-forget). _(**asks for approval**)_ |
| `nova_shard_create` | Create Shard | Create a new shard. |
| `nova_shard_forget` | Forget Shard | Hard soft-delete with provenance log. _(**asks for approval**)_ |
| `nova_shard_get` | Read Shard | Read the full raw content of a shard from disk. _(read-only)_ |
| `nova_shard_get_full` | Read Shard Body | Cold-path full-body fetch. _(read-only)_ |
| `nova_shard_index` | Browse Shard Index | Browse shards using compact metadata rows without loading conversation bodies. _(read-only)_ |
| `nova_shard_interact` | Load Shards into Context | Load shards into context for synthesis. _(read-only)_ |
| `nova_shard_list` | List Shards (legacy dump) | Return a legacy full shard dump. _(read-only)_ |
| `nova_shard_merge` | Merge Shards | Merge multiple shards into a meta-shard. |
| `nova_shard_query_state` | Query Shard State Vector | Query the SQLite shard index by epistemic state vector. _(read-only)_ |
| `nova_shard_search` | Search Shards | Search shards with confidence weighting. _(read-only)_ |
| `nova_shard_summary` | Browse Shards with Synopsis | Browse shards with compact metadata rows plus a short synopsis per shard. _(read-only)_ |
| `nova_shard_update` | Append Turn to Shard | Append to a shard. |
| `nova_shard_validate` | Record Validation Event | Record an epistemic validation event on a shard. |
| `nova_wiki_get` | Read Wiki Page | Read a specific wiki page in full. _(read-only)_ |
| `nova_wiki_ingest` | Ingest Document into Wiki | Ingest a source document into the wiki. |
| `nova_wiki_lint` | Lint Wiki | Health check the wiki. |
| `nova_wiki_list` | List Wiki Pages | List all wiki pages with one-line summaries. _(read-only)_ |
| `nova_wiki_query` | Search Wiki | Semantic search over wiki pages using cosine similarity. _(read-only)_ |
| `nova_wiki_schema` | View or Edit Wiki Taxonomy | View or modify the wiki topic taxonomy. |
<!-- END GENERATED TOOL TABLE -->

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

For all other domains see `forgemaster/SKILL_LIBRARY.md` (24 categories, 324 skills).

---

## Standard Sprint Workflow

```
1. nova_shard_interact(message="[project name] current state")
2. Read design doc → forgemaster-writing-plans
3. Classify tickets → forgemaster-orchestrator
4. Dispatch lanes → forgemaster-parallel-lanes
5. Review results → forgemaster-code-review
6. nova_shard_update(shard_id=...) — write decisions made
7. Maintenance runs itself — NÓTT fires on session start, after a sprint, and
   on a shard-count threshold. Call nova_shard_consolidate() only to force a
   cycle; it asks for approval.
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
- Never commit `.env`, `shard_index.json`, `shard_graph.json`, `nova_usage.jsonl`, `code_index_manifest.json`
- Always use `nova_server.py` — no deprecated servers remain
- Confidence < 0.4 → shard tagged `low_confidence`, excluded from default search. Use `include_low_confidence=True` to recall deliberately
- After creating related shards, wire them with `nova_graph_relate`. Before dependent work, query: `nova_graph_query(target=shard_id, relation_type=depends_on)`
- Every handler is annotated with a model from `mcp/outputs.py`, never `-> str`.
  A handler that can refuse returns `Success | RejectPayload`. Returning a shape
  the annotation does not cover raises `UnexpectedToolError` — the SDK validates
- The wire surface is pinned by `tests/golden/tool_manifest.json`. After an
  intended change: `python utilities/dump_tool_manifest.py --write`, then check
  the diff moved only what you meant to move
- A new resource that serves the same data as a tool must call `_require(<tool>)`
  in `nova_server.py` — resources do not go through the permission context on
  their own

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
- Do not call `nova_shard_consolidate` on a schedule — NÓTT already runs decay,
  compaction and merge detection automatically, and the tool asks for approval.
  Use `dry_run=true` to read the last report without starting a cycle
- Do not start implementation without loading NOVA context first
- Do not end a session without the handoff write
- Do not commit the shards directory — personal data
- Do not use OpenAI models — Haiku for research/docs, Gemini Flash for implementation, Sonnet for architecture/review
- If `CLAUDE_API_KEY` is absent, HUGINN and MUNINN fall back to local embeddings silently
