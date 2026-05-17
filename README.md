# NOVA-Cognition-Framework

![MCP](https://img.shields.io/badge/MCP-server-6B47ED?style=flat-square&logo=anthropic&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)
![Gemini](https://img.shields.io/badge/Gemini-Flash-4285F4?style=flat-square&logo=google&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-ready-2496ED?style=flat-square&logo=docker&logoColor=white)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
![Shard Health](https://github.com/Vintersong/NOVA-Cognition-Framework/actions/workflows/shard-health.yml/badge.svg)

> **Author:** Andrei Moldovean · conceived April 2025 · first public commit March 2026  
> Original concepts: shard memory architecture, HUGINN/MUNINN retrieval pipeline, NÓTT daemon, confidence decay, shard consolidation, Forgemaster orchestration.

---

A unified repository containing **NOVA** (persistent AI memory) and **Forgemaster** (multi-agent orchestration). NOVA is the memory layer. Forgemaster is the execution layer. They share one repo and one data store.

---

## What is NOVA?

NOVA is a persistent memory system for AI agents. It stores conversations and decisions as modular JSON "shards" — each with metadata, confidence scores, and decay rates. A knowledge graph tracks relationships between shards so agents can retrieve contextually relevant memory, not just recent history. A parallel **wiki layer** holds curated, evergreen pages ingested from external sources.

NOVA runs as an MCP server, meaning any MCP-compatible client (Claude Desktop, Claude Code, Cursor) can connect to it and query/write memory using structured tool calls.

**Key capabilities:**
- Semantic shard retrieval via HUGINN (Haiku fast pass) + MUNINN (Sonnet deep rerank) + spreading activation (graph-based third pass)
- Confidence-weighted recall with time-based decay
- Cluster-aware hook recall: top-k walk that collapses same-cluster results and surfaces siblings
- Anthropic prompt-cache prewarm: fires a max_tokens=0 cache-write call so subsequent requests read at ~0.1× cost
- HMAC-SHA256 embedding integrity: signs vectors at ingestion, verifies before MUNINN reranking, logs adversarial events
- NÓTT background daemon: decay, compaction, merge suggestions, graph sync
- Knowledge graph of inter-shard relationships with transitive BFS
- Three-tier discovery: index → summary → full shard (via `summary_index.json`)
- Wiki layer for curated, non-decaying knowledge
- Nidhogg ingestion pipeline with full provenance tracking
- Self-evolution loop (`nova_evolve`) for health-driven planning cycles
- Usage logging per operation to `nova_usage.jsonl`

---

## What is Forgemaster?

Forgemaster is a multi-agent orchestration layer built on top of NOVA. It decomposes tasks into typed tickets and routes each to the optimal model in a defined hierarchy.

Agents run in parallel sandboxed lanes. NOVA is Forgemaster's memory backplane: every sprint reads project context from shards and writes decisions back.

**Key capabilities:**
- Task decomposition from design docs into typed, routable tickets
- Model routing by task type and confidence score, with complexity-keyword override
- Parallel sandboxed execution lanes
- Sprint pipeline writes implementer output to disk when the design doc names a `Target file:`
- Per-call JSONL event log via `FORGEMASTER_EVENT_LOG`
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

## Architecture Diagram

```mermaid
graph TD
    A["MCP Client\n(Claude Desktop / Claude Code / Cursor)"] -->|"36 tool calls"| B["nova_server.py\nMCP Adapter"]

    B --> C
    B -->|"nova_shard_interact"| R
    B --> D

    subgraph C["NOVA Memory Layer"]
        C1["store.py\nShard I/O"] --- C2["graph.py\nKnowledge Graph"]
        C1 --- C3["wiki_tools.py\nWiki Layer"]
        C1 --- C4["facts.py\nFacts Corpus"]
    end

    C1 -->|"nova_shard_index.db"| E[("SQLite Index")]
    C4 -->|"facts_index.db"| E

    subgraph R["Ravens Retrieval Pipeline"]
        R1["HUGINN\nHaiku fast pass"] -->|"score ≥ threshold"| R3["Result"]
        R1 -->|"score < threshold"| R2["MUNINN\nSonnet deep rerank"]
        R2 --> R3
        R2 --> R4["Spreading Activation\nGraph BFS third pass"]
        R4 --> R3
    end

    subgraph N["NÓTT Daemon"]
        N1["Decay"] --> N2["Compaction"]
        N2 --> N3["Merge Scan"]
        N3 --> N4["Adversarial Pass"]
    end

    N -->|"scheduled maintenance"| C1
    ND["Nidhogg\nRepo Ingestion"] --> C1

    subgraph FM["Forgemaster Model Routing"]
        FM1["Sonnet\nArchitecture / Review"]
        FM2["Gemini Flash\nImplementation"]
        FM3["Haiku\nResearch / Docs"]
    end

    D["Forgemaster\nSprint Orchestrator"] --> FM
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
CLAUDE_API_KEY=sk-ant-...
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.5-flash
```

If `CLAUDE_API_KEY` is absent, HUGINN and MUNINN silently fall back to local-only retrieval (token overlap + cosine over local embeddings).

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
        "CLAUDE_API_KEY": "sk-ant-...",
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

### Docker (clean room)

Build the image — embedding model (~80 MB) is downloaded and cached during build so first boot never blocks:

```bash
docker build -t nova:latest .
```

Run interactively (seeds one health-check shard on first boot if `nova_data` volume is empty):

```bash
docker run --rm -i \
  -e CLAUDE_API_KEY=sk-ant-... \
  -e GEMINI_API_KEY=... \
  -v nova_data:/app/data \
  nova:latest
```

Register in Claude Desktop / Claude Code (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "nova": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-e", "CLAUDE_API_KEY=sk-ant-...",
        "-e", "GEMINI_API_KEY=...",
        "-v", "nova_data:/app/data",
        "nova:latest"
      ]
    }
  }
}
```

Or use `docker-compose.yml` (copy your keys into `.env` first):

```bash
docker compose up nova
```

`NOVA_HITL_BROKER=policy` is set in the image by default — irreversible HITL calls are auto-denied rather than blocking the server waiting for terminal input.

---

## Directory Structure

```
NOVA-Cognition-Framework/
  mcp/
    # Core server
    nova_server.py           ← ACTIVE MCP server (registers all 36 tools)
    config.py                ← all env vars and defaults (single source of truth)
    schemas.py               ← Pydantic input models
    models.py                ← shared dataclasses (UsageSummary)
    requirements.txt
    SKILL.md                 ← NOVA cognitive instructions (loaded every session)
    ONBOARDING.md            ← fresh-install flow (triggered when no shards exist)

    # Shard I/O & persistence
    store.py                 ← shard JSON read/write, index management, summary-index layer
    nova_shard_db.py         ← SQLite-backed shard index (nova_shard_index.db)
    shard_format.py          ← shard serialisation helpers (.json ↔ .shard)
    shard_parser.py          ← shard parsing (used by facts.py)
    atomic_io.py             ← atomic file write primitives

    # Knowledge graph
    graph.py                 ← inter-shard graph ops (entities, relations, transitive BFS)

    # Maintenance & lifecycle
    maintenance.py           ← confidence decay, compaction, cosine similarity, merge
    nott.py                  ← NÓTT daemon: decay, compact, merge, graph sync

    # Retrieval & ranking
    ravens.py                ← HUGINN (Haiku fast retrieval) + MUNINN (Sonnet deep rerank)
    recall.py                ← hook recall + cache prewarm + cluster-aware top-k walk
    nova_embeddings_local.py ← local all-MiniLM-L6-v2 embeddings + heuristic summaries
    clustering.py            ← shard community detection (Leiden algorithm)
    arrow_cache.py           ← Arrow-format embedding cache for vectorised cosine
    spreading_activation.py  ← graph-based score propagation as MUNINN third pass
    embedding_integrity.py   ← HMAC-SHA256 signing and verification for shard embeddings

    # Permissions, gating & audit
    permissions.py           ← env-driven tool allow/deny (NOVA_DENIED_TOOLS/PREFIXES)
    capability_gate.py       ← HITL gate keyed to skill verification level
    audit_log.py             ← per-tool HITL audit trail (SQLite, WAL mode)
    access_log.py            ← shard access logging for decay-on-read pass

    # Session & sprint
    session_store.py         ← session CRUD and flush-to-disk
    forgemaster_runtime.py   ← sprint orchestration, real LLM dispatch, biconditional audit
    usage.py                 ← JSONL operation logging
    timeutils.py             ← shared time/date helpers

    # Hook system
    hooks.py                 ← hook registry (SESSION_START, POST_SPRINT, COUNT_THRESHOLD)
    nova_hook_extract.py     ← PostToolUse hook: entity extraction on Edit/Write
    nova_hook_precompact.py  ← pre-compaction preparation hook
    nova_hook_recall.py      ← UserPromptSubmit hook: injects NOVA context into prompts
    nova_hook_stop.py        ← session-stop cleanup hook

    # Tool registry
    tool_registry.py         ← canonical ToolSpec registry; @nova_tool validates names at import

    # Skill verification
    skill_manifest.py        ← @@verification / @@capabilities header parser
    skill_verification.py    ← biconditional post-run audit (Phase 4)

    # MCP tool modules
    evolve.py                ← nova_evolve self-improvement loop
    nidhogg.py               ← nidhogg_ingest/scan/status tools
    facts.py                 ← nova_facts_search / nova_facts_rebuild
    wiki.py / wiki_ingest.py / wiki_tools.py  ← wiki layer model, pipeline, MCP tools
    obsidian_export.py       ← Obsidian vault export logic
    build_summary_index.py   ← batch-build summary_index.json via Haiku
    adversarial.py           ← adversarial shard contradiction testing
    ternary_net.py           ← ternary epistemic memory encoder (experimental)

    # Tests (run with pytest from repo root)
    test_nova.py             ← memory-explorer CLI (not pytest — run directly)
    test_adversarial.py / test_clustering.py / test_quarantine.py
    test_recall.py / test_state_gating.py

    Gemini/
      gemini_mcp.py          ← Gemini Flash tools registered into nova_server

  tests/                     ← main pytest suite (17 test files)
  utilities/                 ← migration helpers, diagnostics, ad-hoc maintenance
  docker/
    entrypoint.sh            ← seeds dummy shard on first boot, starts server
    seed/
      nova_docker_healthcheck.json  ← minimal valid shard for clean-room testing
  shards/                    ← live shard data (never edit directly)
  wiki/                      ← curated markdown pages with YAML frontmatter + [[wikilinks]]
  intake/                    ← drop zone for nidhogg_scan
  nova_sessions/             ← flushed MCP session state
  output/                    ← built artifacts, forgemaster event logs
  facts/                     ← curated .shard files for SQLite pre-filter
  forgemaster/
    AGENTS.md                ← orchestration config and model routing
    SKILL_LIBRARY.md         ← index of all skills across 25 domains
    STANDARDS.md             ← authoring standard for all forgemaster content
    skills/                  ← core orchestration skills (12 files)
    library/                 ← domain skill library (324 files, 25 categories)
    agents/                  ← agent persona definitions (221 personas, 18 divisions)
  docs/                      ← reference and roadmap documents
  Donors/                    ← reference implementations
  Dockerfile                 ← builds the MCP server image
  docker-compose.yml         ← dev/test orchestration
  .env                       ← API keys (never commit)
  .env.example               ← template (committed)
```

---

## NOVA MCP Tools (36)

### Core shard + graph + session (22)

| Tool | Description |
|---|---|
| `nova_shard_interact` | Load shards into context — HUGINN fast pass, MUNINN deep rerank |
| `nova_shard_create` | Create shard with post-write embedding enrichment |
| `nova_shard_update` | Append turn — auto-compaction triggered at threshold |
| `nova_shard_search` | Confidence-weighted keyword + ravens search |
| `nova_shard_index` | Compact browse rows, metadata only |
| `nova_shard_summary` | Browse rows plus a short synopsis per shard |
| `nova_shard_list` | Full raw dump (legacy; prefer index/summary) |
| `nova_shard_get` | Read full shard — no side effects |
| `nova_shard_get_full` | Cold-path full-body fetch — returns summary + conversation body |
| `nova_shard_query_state` | Query SQLite shard index by epistemic state vector (confidence, valence, arousal) |
| `nova_shard_merge` | Merge shards into meta-shard, updates graph |
| `nova_shard_archive` | Soft-archive — excluded from search, preserved on disk |
| `nova_shard_forget` | Hard exclude with provenance log |
| `nova_shard_consolidate` | Full NÓTT cycle: decay + compact + merge suggestions |
| `nova_obsidian_export` | Export shard set as Obsidian-compatible markdown vault |
| `nova_graph_query` | Query knowledge graph (direct or transitive BFS) |
| `nova_graph_relate` | Add directed relation between shards |
| `nova_session_flush` | Persist active session to disk |
| `nova_session_load` | Restore flushed session to memory |
| `nova_session_list` | List all persisted session IDs |
| `nova_forgemaster_sprint` | Full 4-turn sprint pipeline |
| `nova_cache_prewarm` | Fire a max_tokens=0 cache-write call with top-N shard summaries so subsequent requests receive Anthropic cache reads |

Relation types: `influences`, `depends_on`, `contradicts`, `extends`, `references`, `merged_from`, `supersedes`, `corroborated_by`.

### Wiki (6)

| Tool | Description |
|---|---|
| `nova_wiki_schema` | Inspect or edit the wiki page schema |
| `nova_wiki_ingest` | Ingest markdown/source into the wiki |
| `nova_wiki_query` | Search across wiki pages |
| `nova_wiki_get` | Read a single wiki page in full |
| `nova_wiki_list` | List wiki pages (optionally by category) |
| `nova_wiki_lint` | Lint wiki content for schema compliance |

Wiki pages live in `wiki/` as markdown with YAML frontmatter and `[[wikilinks]]`. They are evergreen — they do not decay — and are only created/updated via `nova_wiki_ingest`.

### Nidhogg — repo/document ingestion (3)

| Tool | Description |
|---|---|
| `nidhogg_ingest` | Ingest a single file by path |
| `nidhogg_scan` | Scan `intake/` and ingest all pending files |
| `nidhogg_status` | Show manifest of ingested file hashes |

Nidhogg is non-destructive: it appends a `nidhogg` block to matched shards and never rewrites existing fields. Idempotent via SHA256 manifest.

### Facts (2)

| Tool | Description |
|---|---|
| `nova_facts_search` | Search the SQLite-backed `.shard` facts corpus |
| `nova_facts_rebuild` | Rebuild the facts index from shard sources |

### Evolution (1)

| Tool | Description |
|---|---|
| `nova_evolve` | Run one self-evolution cycle: analyze → verify → commit → govern → plan |

> ⚠ **Side effect:** `nova_evolve` runs `git add` and `git commit` locally when tests pass. On test failure it runs `git checkout -- .` to roll back the staged changes. Nothing is pushed to a remote. Use `dry_run=true` if you just want the director prompt. If `mcp/` changed, it writes `.sdd/runtime/restart_requested`.

### Gemini (2)

Registered into `nova_server` via `mcp/Gemini/gemini_mcp.py`:

| Tool | Description |
|---|---|
| `gemini_execute_ticket` | Send a structured ticket to Gemini Flash |
| `gemini_load_file` | Load a file from disk as codebase context |

### MCP Resources

Read-only resources exposed alongside the tools:

- `nova://skill` — contents of `mcp/SKILL.md`
- `nova://index` — current shard index JSON
- `nova://graph` — current knowledge graph JSON
- `nova://usage` — last 100 log entries + session token totals

---

## Module Architecture

`nova_server.py` is a thin MCP adapter. All business logic lives in dedicated modules:

| Module | Responsibility |
|---|---|
| `config.py` | Single source for all env vars and defaults |
| `schemas.py` | Pydantic input models for core + wiki tools |
| `models.py` | Shared dataclasses (UsageSummary) |
| `tool_registry.py` | Canonical `ToolSpec` registry — `@nova_tool` validates names at import, feeds permissions/audit/docs |
| `store.py` | Shard filesystem I/O, index, summary-index layer, path-traversal guards |
| `graph.py` | Knowledge graph load/save/query/relate/transitive BFS |
| `maintenance.py` | Confidence decay, auto-compaction, cosine similarity, merge candidates |
| `permissions.py` | Env-driven tool allow/deny |
| `hooks.py` | Event-driven hook registry (SESSION_START, POST_SPRINT, COUNT_THRESHOLD) |
| `usage.py` | JSONL operation logging |
| `session_store.py` | Session CRUD and flush-to-disk |
| `forgemaster_runtime.py` | Sprint orchestration — routes tickets to model lanes, writes output |
| `ravens.py` | HUGINN (Haiku fast retrieval) + MUNINN (Sonnet deep rerank) |
| `spreading_activation.py` | Damped BFS over the knowledge graph — MUNINN third retrieval pass with cluster-boundary penalties |
| `recall.py` | Hook recall + cache prewarm + cluster-aware top-k walk (collapses same-cluster results, collects siblings over 2×top_k window) |
| `embedding_integrity.py` | HMAC-SHA256 signing at ingestion, signature verification before MUNINN cosine reranking, adversarial event log |
| `nott.py` | NÓTT daemon — scheduled decay, compact, merge, graph sync (dedicated `ThreadPoolExecutor`, isolated from default executor) |
| `nova_embeddings_local.py` | Local embeddings + heuristic compaction summaries (non-blocking `get_embedding_model_if_ready()` used on enrichment path) |
| `capability_gate.py` | HITL gate — capability membership check + HITL broker (interactive on Unix, `msvcrt` polling on Windows) |
| `audit_log.py` | SQLite HITL audit log — four-state lifecycle + biconditional corpus check |
| `skill_manifest.py` | Parses `@@verification` / `@@capabilities` from skill file headers |
| `evolve.py` | Self-evolution loop, adaptive governor, auto-commit |
| `nidhogg.py` | Document ingestion with provenance |
| `wiki.py` / `wiki_ingest.py` / `wiki_tools.py` | Wiki layer model, pipeline, and MCP tools |
| `build_summary_index.py` | Batch-build `summary_index.json` via Haiku |

---

## Stability: Production vs Experimental

### Production-critical (stable, supported)
- `mcp/nova_server.py` and exported MCP tools
- Core runtime modules: `mcp/store.py`, `mcp/graph.py`, `mcp/maintenance.py`, `mcp/forgemaster_runtime.py`, `mcp/permissions.py`, `mcp/session_store.py`, `mcp/hooks.py`, `mcp/tool_registry.py`
- Retrieval and indexing pipeline: `mcp/ravens.py`, `mcp/recall.py`, `mcp/spreading_activation.py`, `mcp/embedding_integrity.py`, `mcp/nova_embeddings_local.py`, `mcp/build_summary_index.py`
- Wiki and ingestion modules: `mcp/wiki.py`, `mcp/wiki_ingest.py`, `mcp/wiki_tools.py`, `mcp/nidhogg.py`

### Experimental / internal tooling (may change without compatibility guarantees)
- `utilities/` scripts (migration helpers, diagnostics, ad-hoc maintenance tools)
- `mcp/test_nova.py` memory explorer CLI
- Planning/reference docs under `docs/` and donor/reference materials under `Donors/`
- `forgemaster/library/` and `forgemaster/agents/` skill/persona content

---

## Key Environment Variables

> For the complete 56-variable reference with impact explanations, see [docs/CONFIG.md](docs/CONFIG.md).

| Variable | Default | Notes |
|---|---|---|
| `NOVA_SHARD_DIR` | `shards` | Path to shard JSON files |
| `CLAUDE_API_KEY` | — | Powers HUGINN + MUNINN + wiki + summary generation |
| `HUGINN_MODEL` | `claude-haiku-4-5-20251001` | Fast retrieval pass |
| `MUNINN_MODEL` | `claude-sonnet-4-6` | Deep rerank pass |
| `HUGINN_CONFIDENCE_THRESHOLD` | `0.7` | Score ≥ this skips MUNINN |
| `RAVEN_API_TIMEOUT` | `10` | Per-call LLM timeout (seconds) before local fallback |
| `GEMINI_API_KEY` | — | Required for Gemini worker |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Implementation lane model |
| `FORGEMASTER_ORCHESTRATOR_MODEL` | inherits `MUNINN_MODEL` | Override the orchestrator role's model — set to an Opus alias on high-stakes sprints |
| `FORGEMASTER_PLANNER_MODEL` | inherits `MUNINN_MODEL` | Override the planner role's model |
| `FORGEMASTER_REVIEWER_MODEL` | inherits `MUNINN_MODEL` | Override the reviewer role's model |
| `FORGEMASTER_IMPLEMENTER_MODEL` | inherits `GEMINI_MODEL` | Override the implementer role's model; `claude-*` IDs route to Anthropic automatically |
| `NOVA_COMPACT_THRESHOLD` | `30` | Turns before auto-compaction |
| `NOVA_COMPACT_KEEP` | `15` | Recent turns retained after compaction |
| `NOVA_DECAY_RATE` | `0.05` | Confidence decay per 7-day period |
| `NOVA_DECAY_DAYS` | `7` | Days per decay period |
| `NOVA_MERGE_THRESHOLD` | `0.85` | Cosine similarity floor for merge suggestions |
| `NOVA_CONFIDENCE_LOW` | `0.4` | Below this → `low_confidence` tag |
| `NOVA_RECENT_DAYS` | `3` | Within N days → `recent` tag |
| `NOVA_STALE_DAYS` | `14` | Not accessed N days → `stale` tag |
| `NOTT_COUNT_THRESHOLD` | `100` | Shard count triggering NÓTT merge scan |
| `NOVA_DENIED_TOOLS` | — | Comma-separated tool names to block |
| `NOVA_DENIED_PREFIXES` | — | Comma-separated prefixes to block |
| `NOVA_SUMMARY_INDEX_FILE` | `summary_index.json` | Path to summary index |
| `NOVA_SUMMARY_MARKDOWN_FILE` | `summary_index.md` | Path to summary markdown |
| `NOVA_WIKI_DIR` | `wiki` | Wiki pages directory |
| `NOVA_WIKI_SCHEMA` | `wiki_schema.json` | Wiki schema file |
| `NOVA_WIKI_INDEX` | `wiki_index.json` | Wiki embedding index |
| `NOVA_WIKI_ROUTING_MODEL` | `claude-haiku-4-5-20251001` | Wiki ingest routing model |
| `NOVA_WIKI_SYNTHESIS_MODEL` | `claude-sonnet-4-6` | Wiki synthesis model |
| `NIDHOGG_INTAKE_DIR` | `intake` | Nidhogg drop zone |
| `NIDHOGG_MANIFEST_FILE` | `nidhogg_manifest.json` | Ingested-hash manifest |
| `NIDHOGG_SIMILARITY_THRESHOLD` | `0.55` | Shard-match threshold for annotation |
| `FORGEMASTER_EVENT_LOG` | — | Override path for sprint JSONL event log |
| `NOVA_RECALL_CACHE_TTL` | `300` | In-memory recall cache TTL in seconds |
| `NOVA_ACTIVATION_MIN_EDGES` | `10` | Minimum graph edges required to run spreading activation |
| `NOVA_EMBEDDING_HMAC_KEY` | — | Hex or UTF-8 secret for HMAC-SHA256 embedding signing; if unset, signing is skipped |
| `NOVA_EMBEDDING_INTEGRITY_LOG` | `embedding_integrity.jsonl` | Path for adversarial embedding event log |

---

## Error Handling

| Scenario | Behavior |
|---|---|
| `CLAUDE_API_KEY` absent | HUGINN and MUNINN silently fall back to local-only retrieval (token overlap + cosine over local all-MiniLM-L6-v2 embeddings). No error is raised; retrieval quality is reduced. |
| HMAC signature mismatch | The embedding is rejected before MUNINN reranking. The event is written to `embedding_integrity.jsonl` (shard ID, timestamp, expected vs. actual signature). Retrieval continues without the tainted vector. |
| MCP client disconnection | The server continues running. The stop hook (`nova_hook_stop.py`) flushes active session state to `nova_sessions/` so the next `nova_session_load` can resume where the session left off. |
| API rate limit / timeout | `RAVEN_API_TIMEOUT` (default `10`s) controls the per-call LLM timeout. On timeout, the MUNINN step is skipped and HUGINN's local-pass result is returned. |
| Shard fails adversarial pass | The shard is quarantined for `NOVA_QUARANTINE_HOURS` (default `48`h). During quarantine its retrieval score is multiplied by `NOVA_QUARANTINE_PENALTY` (default `0.5`) and it is excluded from default search. It graduates automatically if the next NÓTT adversarial pass clears it. |

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

- Never manually edit files in `shards/` or `wiki/` — always use the MCP tools
- Auto-generated files, never commit: `shard_index.json`, `shard_graph.json`, `summary_index.json`, `summary_index.md`, `wiki_index.json`, `nidhogg_manifest.json`, `nova_usage.jsonl`, `evolve_cycles.jsonl`, `evolve.json`, `embedding_integrity.jsonl`
- `.env` contains API keys — never commit it
- `test_nova.py` is a memory-explorer CLI, not a pytest suite — run it with `python mcp/test_nova.py`
- See `CLAUDE.md` for operational instructions and sprint workflow; see `docs/ROADMAP.md` for shipped-vs-planned split
