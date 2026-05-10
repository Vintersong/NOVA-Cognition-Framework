# NOVA-Cognition-Framework — Design Patterns & Programming Cheat Sheet

> Quick reference for developers working in or extending the NOVA + Forgemaster system.
> Generated from a full repo audit of `mcp/`, `forgemaster/`, and `utilities/`.

---

## 1. Programming Paradigms

| Paradigm | Where Used | Details |
|---|---|---|
| **Async-first** | `nova_server.py`, `ravens.py`, `nott.py`, `hooks.py` | All MCP tool handlers are `async def`. Uses `asyncio.wait_for()` (timeout guards), `asyncio.create_task()` (fire-and-forget), `asyncio.to_thread()` (blocking I/O) |
| **Functional / Pure functions** | `store.py`, `graph.py`, `maintenance.py`, `config.py` | No classes, no shared mutable state. All mutations go through explicit save calls |
| **Immutable data flow** | `models.py`, `session_store.py`, `permissions.py` | `@dataclass(frozen=True)` everywhere. `add_turn()` returns a new instance rather than mutating |
| **Declarative validation** | `schemas.py` | Pydantic `BaseModel` with `ConfigDict(extra='forbid')`. Cross-field checks via `@model_validator` |
| **Procedural with OOP boundaries** | `nova_server.py`, `forgemaster_runtime.py` | Business logic in plain functions; OOP used only for encapsulating state (daemons, registries) |
| **Concurrent / threaded** | `nott.py`, `nova_embeddings_local.py` | `ThreadPoolExecutor` (2 workers in NÓTT); `threading.Lock` for singleton model load |
| **Subprocess orchestration** | `evolve.py` | Shells out to `pytest` and `git`; stash-based rollback on failure |
| **Config-driven / environment-driven** | `config.py` | All constants read from env vars at import time; no classes, flat namespace of 50+ constants |

---

## 2. Design Patterns

### GoF Patterns

| Pattern | File(s) | Example |
|---|---|---|
| **Service Locator** | `nova_server.py` | Process-scoped singletons `_huginn`, `_muninn`, `_nott`, `_hooks`, `_session_store` initialised after env setup |
| **Observer / Event-Driven** | `hooks.py` | `NovaHookRegistry` dispatches `SESSION_START`, `POST_SPRINT`, `COUNT_THRESHOLD` events; handlers registered once, fired repeatedly |
| **Repository** | `store.py`, `session_store.py` | All shard I/O behind `load_shard()` / `save_shard()` / `update_index()`. Each session is a separate JSON file |
| **Value Object (Immutable DTO)** | `models.py`, `session_store.py` | `UsageSummary` (frozen dataclass); `NovaSession` (frozen dataclass with tuple-based message store) |
| **Factory (classmethod)** | `session_store.py` | `NovaSession.new()`, `NovaSession.from_dict()` |
| **Strategy** | `forgemaster_runtime.py`, `config.py` | `_ROLE_TO_MODEL` dict and `_ROUTING_TABLE` select model by task type; swap via env var without code change |
| **Decorator / Permission Guard** | `nova_server.py` | `@mcp.tool(name="...")` registers handlers; `_permission_context.blocks()` and `_gate_check()` wrap each tool |
| **Context Object** | `permissions.py` | `ToolPermissionContext` (frozen): dual-level matching by exact tool name or prefix |
| **State Machine** | `session_store.py` | Sessions flow: create (memory) → update (memory) → flush (disk) → load (memory) |

### AI / System-Specific Patterns

| Pattern | File(s) | Example |
|---|---|---|
| **Pipeline** | `forgemaster_runtime.py` | 4-turn sprint: orchestrate → plan → implement → review. Each turn output feeds the next as context |
| **Two-Pass Retrieval** | `ravens.py` | HUGINN (Haiku fast pre-filter, local scoring) → MUNINN (Sonnet deep re-rank). Confidence gate skips second pass if score ≥ threshold |
| **Fallback Chain** | `ravens.py`, `wiki_tools.py`, `utilities/autoresearch.py` | LLM API → local embeddings → keyword overlap. Gemini primary → LM Studio Qwen fallback in autoresearch |
| **Lazy Initialization + Thread-safe Singleton** | `nova_embeddings_local.py` | `_model_lock` (threading.Lock) + background prewarm daemon; `get_embedding_model_if_ready()` returns `None` non-blocking if lock held |
| **Background Enrichment** | `nova_server.py` | Foreground shard save for durability; embedding + summary generation scheduled as `asyncio.create_task()` — never blocks the response |
| **Per-resource Async Locking** | `nova_server.py` | `WeakValueDictionary` of `asyncio.Lock` keyed by `shard_id`; prevents concurrent writes to the same shard |
| **Append-only Log** | `usage.py`, `nidhogg.py` | JSONL format (one JSON object per line); nidhogg provenance blocks only ever appended, never overwritten |
| **Idempotency via SHA-256 Manifest** | `nidhogg.py` | `nidhogg_manifest.json` stores file hashes; same file never re-ingested |
| **Chunking + Mean-pool** | `nidhogg.py` | Long docs split on paragraph boundaries; chunk embeddings averaged into a single document embedding |
| **Adaptive Governance** | `evolve.py` | `AdaptiveGovernor` boosts focus-area weights based on health signals (low confidence, failing tests, nidhogg backlog); diminishing-return decay applied |
| **Prompt Caching** | `wiki_ingest.py` | Anthropic `cache_control` on system prompts shared across a batch; amortises API cost |
| **Pass-based Daemon** | `nott.py` | NÓTT runs separate passes: `_decay_pass`, `_compact_pass`, `_merge_pass`, `_quarantine_pass`, `_cluster_pass`, `_adversarial_pass`. Each pass is independently async-wrapped |
| **XML Score Parsing** | `ravens.py` | LLM returns `<score id="shard_id" value="0.0-1.0">reasoning</score>`; parsed with regex |
| **Graph BFS** | `graph.py` | Adjacency list + visited-set BFS for transitive `query_graph_transitive()` |
| **Canonical Edge Deduplication** | `graph.py` | Symmetric relation types (contradicts, merges_with) sort endpoints before storing — prevents A→B and B→A duplicates |
| **Skeleton / Streaming Read** | `store.py` | `read_shard_skeleton()` uses `ijson` for streaming parse of large shards; avoids full JSON load |
| **Stash-based Rollback** | `evolve.py` | `git stash push` (recoverable) on pytest failure; never uses destructive `checkout .` |

---

## 3. Architecture / System Patterns

### Memory Layers

```
┌─────────────────────────────────────────────────────────────┐
│  Conversational Shards  (shards/*.json)                     │
│  • Confidence decay (exponential, floor 0.1)                │
│  • Auto-compaction at N turns                               │
│  • Searchable via HUGINN + MUNINN two-pass retrieval        │
├─────────────────────────────────────────────────────────────┤
│  Wiki Layer  (wiki/*.md)                                    │
│  • Evergreen — no decay, no archiving                       │
│  • Synthesised from external sources via Haiku→Sonnet       │
│  • [[slug]] wikilinks for cross-page awareness              │
├─────────────────────────────────────────────────────────────┤
│  Knowledge Graph  (shard_graph.json)                        │
│  • Directed edges: influences, depends_on, contradicts, …   │
│  • Symmetric types canonically deduplicated                 │
│  • BFS for transitive relationship queries                  │
├─────────────────────────────────────────────────────────────┤
│  Local Embeddings  (all-MiniLM-L6-v2, 80 MB, CPU-only)     │
│  • Stored inside shard context.embedding                    │
│  • Shared across MUNINN rerank and nidhogg ingestion        │
└─────────────────────────────────────────────────────────────┘
```

### Forgemaster Orchestration Stack

```
Human (design doc / feature request)
    ↓
nova_shard_interact()  ← load project context from NOVA
    ↓
forgemaster-orchestrator  ← decompose into typed tickets
    ↓
forgemaster-parallel-lanes  ← dispatch independent tickets concurrently
    ├── Sonnet Lane  (architecture, review, ambiguous)
    ├── Gemini-Flash Lane  (bounded impl, boilerplate)
    └── Haiku Lane  (research, docs, fast reads)
    ↓
forgemaster-code-review (Stage 1: spec | Stage 2: quality)
    ↓
forgemaster-qa-review  (Stage 3: structural + LLM anti-patterns)
    ↓
nova_shard_update()  ← write decisions and outcomes back to NOVA
```

### Model Routing Table

| Task Type | Model | Rationale |
|---|---|---|
| Architecture, ambiguous requirements, code review | `claude-sonnet` | Complex judgment, cross-cutting concerns |
| Clear spec, 1–3 files, boilerplate, structured output | `gemini-flash` | Fast, deterministic, cost-efficient |
| Research, documentation, fast synthesis | `claude-haiku` | Broad knowledge, cheap, fast |
| Confidence < 0.65 on any ticket | Escalate to `claude-sonnet` | Auto-escalation floor enforced by gemini worker |
| Hard verifiable reasoning (math, algorithmic) | `HeavySkill` | K=3 parallel Haiku thinkers → Sonnet deliberation |

**Hard rule:** OpenAI models are banned across the entire system.

### Confidence Lifecycle

```
Shard created  →  confidence = 1.0
    ↓  (per 7-day period, NÓTT decay pass)
confidence *= (1 - NOVA_DECAY_RATE)  # default 0.05/period, floor 0.1
    ↓
confidence < NOVA_CONFIDENCE_LOW (0.4)  →  tag: low_confidence
    ↓
confidence < NOVA_RECENT_DAYS (3d)  →  tag: recent
    ↓
not accessed > NOVA_STALE_DAYS (14d)  →  tag: stale
```

### Session Handoff Protocol

Before context limit is reached, write to NOVA:

```
CURRENT STATE: branch, last ticket, test status
IN PROGRESS: what was started, what remains
DECISIONS MADE: key choices and rationale
NEXT ACTION: single unambiguous first step for next session
```

Next session always starts with `nova_shard_interact(message="[project] current state")`.

---

## 4. Data Modeling

| Format | Used For | Key Rules |
|---|---|---|
| **Pydantic BaseModel** | All MCP tool inputs (`schemas.py`) | `ConfigDict(extra='forbid')` on every schema; `@model_validator` for cross-field checks |
| **Frozen dataclass** | `UsageSummary`, `NovaSession`, `ToolPermissionContext`, `RetrievalResult` | Mutations return new instances; no in-place modification |
| **JSON shard** | `shards/*.json` | Flat dict: `guiding_question`, `meta_tags`, `conversation_history`, `context` (embedding, summary, topics) |
| **JSONL** | `nova_usage.jsonl`, forgemaster event log | One JSON object per line; append-only |
| **YAML frontmatter** | `wiki/*.md` | Controlled subset: scalars + inline/block lists. Fields: slug, title, tags, updated, sources, body |
| **Adjacency list** | `shard_graph.json` | Entities dict + relations list. Symmetric edges canonically stored by sorted endpoints |
| **Wikilink** | Wiki pages | `[[slug]]` notation; extracted via regex for cross-reference tracking |
| **SHA-256 manifest** | `nidhogg_manifest.json` | Idempotency index keyed by file path → hash |

---

## 5. Security Hardening

| Technique | File(s) | Implementation |
|---|---|---|
| **Path traversal prevention** | `store.py`, `forgemaster_runtime.py`, `nidhogg.py` | `Path.resolve().is_relative_to(allowed_root)` before every file read/write |
| **Tool permission gating** | `permissions.py`, `nova_server.py` | `ToolPermissionContext`: dual-level matching (exact tool name OR prefix); `_permission_context.blocks()` check per handler |
| **Capability gates** | `nova_server.py`, `forgemaster_runtime.py` | `_gate_check()` before Forgemaster sprints; `fs.write.irrev` capability required for implementation commits |
| **Env-var allowlists** | `config.py` | `FORGEMASTER_WRITE_ROOTS`, `NIDHOGG_ALLOWED_ROOTS`, `GEMINI_OUTPUT_DIR` restrict writable paths |
| **Sandbox confinement** | `Gemini/gemini_mcp.py` | Gemini output restricted to `workspace/` subdirectory; lazy `.env` reload at call time |
| **Secrets isolation** | `.gitignore` | `.env`, `shard_index.json`, `shard_graph.json`, `nova_usage.jsonl` never committed |

---

## 6. AI / LLM Integration Patterns

| Pattern | File(s) | Detail |
|---|---|---|
| **Multi-provider dispatch** | `forgemaster_runtime.py` | Model name prefix routes: `claude-*` → Anthropic SDK; `gemini-*` → Google GenAI SDK |
| **Confidence-based gating** | `ravens.py` | HUGINN score ≥ `HUGINN_CONFIDENCE_THRESHOLD` (0.7) → skip MUNINN pass entirely |
| **Skill-file injection** | `forgemaster_runtime.py` | Markdown skill files loaded as system prompt preamble; user content appended after `---` separator |
| **Structured XML output** | `ravens.py` | `<score id="shard_id" value="0.0-1.0">reason</score>` parsed by regex |
| **Prompt caching** | `wiki_ingest.py` | Anthropic `cache_control` on `_ROUTING_SYSTEM` and `_SYNTHESIS_SYSTEM` prompts; single client reused across batch |
| **Local-first embeddings** | `nova_embeddings_local.py` | `all-MiniLM-L6-v2` (CPU-only, ~80 MB) prewarms at startup; API used only for Sonnet rerank |
| **Adversarial pass** | `nott.py` | NÓTT sends top-N high-confidence shards to Gemini Flash to hunt contradictions; creates `contradicts` graph edges |
| **HeavySkill (K=3 deliberation)** | `forgemaster/skills/forgemaster-heavyskill.md` | 3 Haiku thinkers in parallel (no shared context) → serialised outputs → Sonnet deliberation in-context |
| **Privacy-preserving query log** | `ravens.py` | SHA-256 hash of query logged by default; plaintext preview opt-in via `NOVA_LOG_QUERY_PREVIEW` |
| **Token accounting** | `models.py`, `nova_server.py` | Input/output tokens logged per LLM call; word-count estimation fallback when offline |

---

## 7. Testing & Quality Gates

### Structural QA Thresholds (`forgemaster-qa-review.md`)

| Metric | FLAG | BLOCK |
|---|---|---|
| Cyclomatic complexity | ≥ 10 | ≥ 15 |
| Function length (lines) | ≥ 50 | ≥ 80 |
| Nesting depth | > 3 | > 4 |
| Code duplication ratio | ≥ 15% | ≥ 25% |
| Test coverage (new code) | < 80% | < 60% |

### LLM-Generated Code Anti-Patterns

| Anti-Pattern | Detection Rule |
|---|---|
| Monolithic Function Inflation | cyclomatic > `max(10, log₂(lines))` |
| Silent State Mutation | writes to non-local without `global`/`nonlocal` |
| Cascading Error Suppression | `except: pass` or `except Exception:` with no logging |
| Redundant Abstraction Layering | call chains > 2 layers with no added logic |
| Symmetric Branch Duplication | if/elif/else bodies share ≥ 80% syntactic overlap |
| Loop-Carried State Without Guard | accumulator reused without invalidation check |
| Magic Numbers | integers < 5 or > 50 appearing inline |

### Code Review Stages

1. **Stage 1 — Spec Compliance** (binary: pass/fail). Does the code implement exactly what the spec said?
2. **Stage 2 — Quality** (dimensions: correctness, clarity, security, NOVA integration, maintainability).
3. **Stage 3 — Structural QA** (thresholds + LLM anti-patterns above).

Verdict levels: `REQUIRED` (blocks merge) · `SUGGESTION` (non-blocking) · `NOTE` (informational)

### Test-Gated Auto-Commit (`evolve.py`)

- `pytest` must pass before `git commit`
- On failure: `git stash push` (recoverable) → no commit
- Only files in `EVOLVE_COMMIT_ROOTS` allowlist are staged
- mcp/ changes set a `restart_requested` marker file

---

## 8. Key Files at a Glance

| File | Role |
|---|---|
| `mcp/nova_server.py` | MCP entry point — singletons, 31-tool registry, per-shard async locking |
| `mcp/config.py` | Single source of truth for all env vars and constants |
| `mcp/schemas.py` | Pydantic input models for every MCP tool |
| `mcp/models.py` | Frozen `UsageSummary` — immutable token accounting |
| `mcp/store.py` | Shard Repository — I/O, index, state-aware gating, path safety |
| `mcp/graph.py` | Knowledge graph — adjacency list, BFS, canonical edge dedup |
| `mcp/maintenance.py` | Confidence decay, compaction, cosine-similarity merge detection |
| `mcp/hooks.py` | Observer event registry — fire-and-forget + emit_wait() |
| `mcp/session_store.py` | Frozen session DTO + flush/load lifecycle |
| `mcp/forgemaster_runtime.py` | 4-turn sprint Pipeline, multi-model routing, skill injection |
| `mcp/ravens.py` | HUGINN + MUNINN two-pass retrieval |
| `mcp/nott.py` | NÓTT background daemon — pass-based maintenance |
| `mcp/nova_embeddings_local.py` | Lazy singleton embedding model + prewarm thread |
| `mcp/evolve.py` | Adaptive governance loop + test-gated auto-commit |
| `mcp/nidhogg.py` | Idempotent 6-stage document ingestion pipeline |
| `mcp/wiki_ingest.py` | Two-pass (Haiku route → Sonnet synthesise) with prompt caching |
| `mcp/wiki.py` | WikiPage dataclass + YAML frontmatter CRUD |
| `mcp/permissions.py` | Immutable `ToolPermissionContext` — dual-level tool gating |
| `mcp/usage.py` | Append-only JSONL operation logger |
| `mcp/Gemini/gemini_mcp.py` | Gemini Flash worker — lazy init, sandboxed workspace |
| `forgemaster/AGENTS.md` | Orchestration config + model routing table |
| `forgemaster/STANDARDS.md` | Authoring standard for skills, agents, hooks |
| `forgemaster/SKILL_LIBRARY.md` | Master index — 208 skills across 15 domains |

---

## 9. Quick-Reference: NOVA Maintenance Cycle

```
Every session start  →  NÓTT SESSION_START trigger
                         └─ light decay pass only

Every N tool calls   →  NÓTT COUNT_THRESHOLD trigger
                         └─ decay + compact pass

After sprint         →  NÓTT POST_SPRINT trigger
                         └─ full cycle: decay + compact + merge + quarantine + cluster + adversarial

Every 3 sprints      →  nova_shard_consolidate() (explicit call)
                         └─ full NÓTT cycle + graph sync + merge report returned to user
```

---

*Last updated: 2026-05-10 — generated from full audit of NOVA-Cognition-Framework mcp/, forgemaster/, utilities/*
