# NOVA-Cognition-Framework — Deep Repository Analysis

> **Date:** March 26, 2026  
> **Scope:** Full repository analysis covering architecture, code, data, configuration, and design intent

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Repository Identity and Purpose](#2-repository-identity-and-purpose)
3. [High-Level Architecture](#3-high-level-architecture)
4. [Directory Structure Map](#4-directory-structure-map)
5. [NOVA Memory System (v2)](#5-nova-memory-system-v2)
   - 5.1 [Core Design Philosophy](#51-core-design-philosophy)
   - 5.2 [Shard Data Model](#52-shard-data-model)
   - 5.3 [MCP Server — nova_server_v2.py](#53-mcp-server--nova_server_v2py)
   - 5.4 [11 Exposed MCP Tools](#54-11-exposed-mcp-tools)
   - 5.5 [Confidence and Decay System](#55-confidence-and-decay-system)
   - 5.6 [Auto-Compaction Engine](#56-auto-compaction-engine)
   - 5.7 [Knowledge Graph Layer](#57-knowledge-graph-layer)
   - 5.8 [Local Embedding Module](#58-local-embedding-module)
   - 5.9 [Usage Tracking](#59-usage-tracking)
   - 5.10 [MCP Resources (Read-only endpoints)](#510-mcp-resources-read-only-endpoints)
6. [Forgemaster Orchestration Layer](#6-forgemaster-orchestration-layer)
   - 6.1 [System Overview](#61-system-overview)
   - 6.2 [Model Routing Strategy](#62-model-routing-strategy)
   - 6.3 [Core Skill Library (9 skills)](#63-core-skill-library-9-skills)
   - 6.4 [Agent Persona System](#64-agent-persona-system)
   - 6.5 [Extended Skill Library (150+ skills)](#65-extended-skill-library-150-skills)
   - 6.6 [Sprint Workflow](#66-sprint-workflow)
   - 6.7 [Forgemaster Content Standards](#67-forgemaster-content-standards)
7. [Python Utilities Layer](#7-python-utilities-layer)
   - 7.1 [shard_index.py](#71-shard_indexpy)
   - 7.2 [context_extractor.py](#72-context_extractorpy)
   - 7.3 [Supporting Scripts](#73-supporting-scripts)
8. [Migration Tooling](#8-migration-tooling)
9. [Live Shard Data Analysis](#9-live-shard-data-analysis)
10. [Runtime Files and Configuration](#10-runtime-files-and-configuration)
11. [Dependency Analysis](#11-dependency-analysis)
12. [System Integration Map](#12-system-integration-map)
13. [Design Patterns and Architectural Decisions](#13-design-patterns-and-architectural-decisions)
14. [Strengths and Limitations](#14-strengths-and-limitations)
15. [Key Observations](#15-key-observations)

---

## 1. Executive Summary

**NOVA-Cognition-Framework** is a personal AI memory and multi-agent orchestration system built by a single developer. It consists of two tightly integrated systems:

- **NOVA v2** — A persistent, modular memory server that stores conversations as JSON "shards," each with confidence scores, embeddings, decay mechanics, and inter-shard relationships tracked in a knowledge graph. Exposed as an MCP (Model Context Protocol) server with 11 tools.

- **Forgemaster** — A multi-agent orchestration framework that uses NOVA as its persistent memory backplane, routes tasks to optimal LLM models (Claude, Gemini Flash, GPT-4o), and manages execution through a library of 150+ specialized procedural skills across 13 domains.

The repository also contains **424 live shard files** migrated from ChatGPT conversation exports — actual personal knowledge and thinking — making this a working, populated system rather than a blank template.

The core thesis this system embodies is: **"Structure over processing power — intelligence emerges from recursive interaction with well-organized memory."** This philosophy is baked into every architectural decision.

---

## 2. Repository Identity and Purpose

| Field | Value |
|---|---|
| **Name** | NOVA-Cognition-Framework |
| **Owner** | Personal project (Moldo) |
| **Primary language** | Python |
| **Protocol** | Model Context Protocol (MCP) |
| **Active server** | `mcp/nova_server_v2.py` |
| **Live shard count** | 424 JSON shards |
| **Total content files** | 814+ (forgemaster) |
| **MCP tools exposed** | 11 |
| **Skill files** | 208 reusable skills across 13 domain categories |
| **Agent personas** | 357 across 18 domain folders |

**What this system is NOT:**
- Not a hosted service or SaaS product
- Not a database (no SQL, no vector DB dependency)
- Not dependent on any cloud API for core memory operations (runs offline after first model download)

**What this system IS:**
- A personal cognitive extension: externalized, structured, persistent AI memory
- A multi-agent task router that uses that memory as context for every sprint
- A proof-of-concept for the thesis that memory architecture, not model scale, is the key AI frontier

---

## 3. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                     YOU (Design doc / Task request)                 │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌───────────────────────────────────────────────────────────────────┐
│               FORGEMASTER ORCHESTRATION LAYER                     │
│  ┌──────────────────────┐   ┌──────────────────────────────────┐  │
│  │ Orchestrator         │   │ Parallel Lane Dispatcher         │  │
│  │ (task decomposition, │──▶│ (sandboxed execution,            │  │
│  │  ticket routing)     │   │  result collection)              │  │
│  └──────────────────────┘   └──────────────────────────────────┘  │
│                                          │                         │
│           ┌──────────────────────────────┼──────────────────────┐  │
│           ▼                              ▼                      ▼  │
│   claude-sonnet               gemini-flash               gpt-4o   │
│  (architecture, review)  (implementation, boilerplate) (research) │
└───────────────────────────────────────────────────────────────────┘
                                │
                    reads from / writes to
                                │
                                ▼
┌───────────────────────────────────────────────────────────────────┐
│                    NOVA v2 MEMORY SYSTEM (MCP Server)             │
│                                                                   │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐   │
│  │ Shard Store  │  │ Knowledge    │  │ Automation Layer       │   │
│  │ (424 JSON    │  │ Graph        │  │ - Confidence decay     │   │
│  │ shards)      │  │ (shard_      │  │ - Auto-compaction      │   │
│  │              │  │ graph.json)  │  │ - Embedding enrichment │   │
│  └──────────────┘  └──────────────┘  └───────────────────────┘   │
│                                                                   │
│  ┌──────────────┐  ┌──────────────┐                              │
│  │ Shard Index  │  │ Usage Log    │                              │
│  │ (shard_      │  │ (nova_usage  │                              │
│  │ index.json)  │  │ .jsonl)      │                              │
│  └──────────────┘  └──────────────┘                              │
└───────────────────────────────────────────────────────────────────┘
                                │
                    local embeddings via
                    sentence-transformers
                    (all-MiniLM-L6-v2, 80MB, offline)
```

---

## 4. Directory Structure Map

```
NOVA-Cognition-Framework/
│
├── CLAUDE.md                    ← Claude Code instructions for this repo
├── README.md                    ← Public documentation
├── LICENSE
├── nova_usage.jsonl             ← Runtime: operation log (JSONL, append-only)
├── shard_index.json             ← Runtime: auto-generated index of all shards
│
├── mcp/                         ← NOVA MCP SERVER (primary)
│   ├── nova_server_v2.py        ← ACTIVE server (v2, 11 tools)
│   ├── nova_embeddings_local.py ← Local sentence-transformers embedding module
│   ├── nova_server.py           ← v1 reference only, DO NOT USE
│   ├── SKILL_v2.md              ← ACTIVE skill definition for NOVA agents
│   ├── SKILL.md                 ← v1 reference only
│   └── requirements.txt         ← mcp[cli], pydantic, sentence-transformers, python-dotenv
│
├── python/                      ← Utility scripts
│   ├── shard_index.py           ← Index manager (imported by nova_server_v2)
│   ├── context_extractor.py     ← Batch enrichment via OpenAI GPT-4 + ada-002
│   ├── main.py                  ← (entry point, not analyzed in detail)
│   ├── rename_shards.py         ← Batch rename utility
│   ├── dedup_json.py            ← Deduplication utility
│   └── requirements.txt
│
├── shards/                      ← LIVE SHARD DATA (424 JSON files, DO NOT EDIT MANUALLY)
│   ├── chatgpt_ai_ml_*.json     ← Migrated from ChatGPT exports
│   └── ...
│
├── tools/
│   └── chatgpt_to_nova.py       ← One-shot migration utility (ChatGPT → NOVA shards)
│
├── forgemaster/                 ← ORCHESTRATION LAYER
│   ├── AGENTS.md                ← Global agent configuration (YAML frontmatter + docs)
│   ├── SKILL_LIBRARY.md         ← Master skill index (150+ skills across 13 domains)
│   ├── STANDARDS.md             ← Content authoring standards and schemas
│   ├── agents/                  ← 357 agent persona definitions (18 domain folders)
│   │   ├── academic/
│   │   ├── autonomous-agents/
│   │   ├── design/
│   │   ├── engineering/
│   │   ├── game-development/
│   │   ├── integrations/
│   │   ├── marketing/
│   │   ├── paid-media/
│   │   ├── product/
│   │   ├── project-management/
│   │   ├── research/
│   │   ├── sales/
│   │   ├── spatial-computing/
│   │   ├── specialized/
│   │   ├── strategy/
│   │   ├── support/
│   │   ├── testing/
│   │   └── examples/
│   ├── skills/                  ← 9 core Forgemaster skills
│   │   ├── forgemaster-orchestrator.md
│   │   ├── forgemaster-parallel-lanes.md
│   │   ├── forgemaster-writing-plans.md
│   │   ├── forgemaster-implementation.md
│   │   ├── forgemaster-systematic-debugging.md
│   │   ├── forgemaster-verification.md
│   │   ├── forgemaster-git-workflow.md
│   │   ├── forgemaster-code-review.md
│   │   └── forgemaster-nova-session-handoff.md
│   ├── library/                 ← 208 SKILL.md files (external repos copied in)
│   │   ├── agentic-workflows/   (20 skills)
│   │   ├── engineering/         (12 language/framework skills)
│   │   ├── infrastructure/      (8 DevOps skills)
│   │   ├── data-ai-ml/
│   │   ├── databases/
│   │   ├── frontend-design/
│   │   ├── security/
│   │   ├── game-dev/
│   │   ├── project-management/
│   │   ├── code-intelligence/
│   │   ├── autonomous-agents/
│   │   ├── communication/
│   │   └── observability/
│   ├── docs/
│   ├── rules/                   ← 20 coding standard files and hooks config
│   ├── slash-commands/          ← 84 Claude slash-command prompts
│   ├── templates/               ← 60 project scaffolding and planning templates
│   └── workflows/               ← 56 step-by-step workflow processes
│
└── docs/                        ← Reference documentation
    ├── awesome-agent-skills-index.md
    ├── awesome-claude-code-index.md
    ├── awesome-claude-skills-index.md
    ├── hive-*.md                ← Hive multi-agent reference docs
    ├── openfang-*.md            ← OpenFang architecture references
    └── ...
```

---

## 5. NOVA Memory System (v2)

### 5.1 Core Design Philosophy

NOVA's philosophy is stated explicitly in `mcp/SKILL_v2.md`:

> **"Structure over processing power. Intelligence emerges from recursive interaction with well-organized memory, not from larger context windows. Memory is reconstructed, not retained. The processor is stateless by design — this is a feature, not a limitation."**

This is not generic design documentation — it directly traces to conversations in the live shards where the author predicted (before Sam Altman's 2025 statement) that persistent memory architecture, not reasoning improvements, would be the next major AI breakthrough. NOVA v2 is the author's working implementation of that thesis.

The key architectural consequence: **the LLM is deliberately kept stateless.** It doesn't accumulate state across sessions. Instead, state lives in the shard system, and each session reconstructs context by loading the relevant shards. This mirrors how human memory works — not retained wholesale but reconstructed from structured storage.

### 5.2 Shard Data Model

Every piece of memory is a **shard** — a self-contained JSON document stored as a flat file in the `shards/` directory.

```json
{
  "shard_id": "string — unique identifier, derived from filename",
  "guiding_question": "string — the north star of this shard (what problem/question this addresses)",
  "conversation_history": [
    {
      "timestamp": "ISO 8601",
      "user": "user message text",
      "ai": "AI response text"
    }
  ],
  "meta_tags": {
    "intent": "reflection | planning | research | brainstorm | archive | forgotten | meta_synthesis",
    "theme": "string — domain label",
    "usage_count": 0,
    "last_used": "ISO 8601",
    "confidence": 1.0,
    "enrichment_status": "enriched_local | pending | pending_no_model | failed",
    "last_compacted": "ISO 8601 (optional — set when auto-compaction runs)",
    "compacted_turn_count": 0
  },
  "context": {
    "summary": "string — generated by enrichment hook",
    "topics": ["tag1", "tag2", "tag3"],
    "conversation_type": "string",
    "embedding": [0.012, ...],
    "last_context_update": "ISO 8601",
    "embedding_model": "all-MiniLM-L6-v2"
  }
}
```

**Key design decisions:**
- **Flat file storage** — No database dependency. Every shard is a `.json` file readable and auditable by humans.
- **Guiding question as north star** — Each shard has a single orienting question that defines its scope. This acts as the primary retrieval anchor.
- **Confidence as a float (0.1–1.0)** — Not binary active/inactive. Confidence decays continuously with disuse, causing shards to sink naturally in relevance without being deleted.
- **Intent tagging** — Shards can be `reflection`, `planning`, `research`, `brainstorm`, `archive`, or `forgotten`. This allows filtering by purpose, not just content.
- **Compaction summary preserved** — When a shard is auto-compacted, the summary of the removed turns is prepended to the context summary, so no information is permanently lost.

### 5.3 MCP Server — nova_server_v2.py

The server runs via `mcp[cli]` using the `FastMCP` framework from the MCP SDK. It is fully asynchronous (all tool handlers are `async def`).

**Environment configuration (all overridable via env vars):**

| Variable | Default | Purpose |
|---|---|---|
| `NOVA_SHARD_DIR` | `<repo_root>/shards` | Path to shard JSON files |
| `NOVA_INDEX_FILE` | `<repo_root>/shard_index.json` | Path to shard index |
| `NOVA_GRAPH_FILE` | `<repo_root>/shard_graph.json` | Path to knowledge graph |
| `NOVA_USAGE_LOG` | `<repo_root>/nova_usage.jsonl` | Usage log path |
| `NOVA_MAX_FRAGMENTS` | `10` | Max conversation fragments loaded per shard |
| `NOVA_COMPACT_THRESHOLD` | `30` | Number of turns before auto-compaction triggers |
| `NOVA_COMPACT_KEEP` | `15` | Most-recent turns to keep after compaction |
| `NOVA_DECAY_RATE` | `0.05` | Confidence decay rate per period |
| `NOVA_DECAY_DAYS` | `7` | Days between decay periods |
| `NOVA_MERGE_THRESHOLD` | `0.85` | Cosine similarity threshold for merge suggestions |

**Path resolution:** The server anchors its default paths to `Path(__file__).parent.parent` (repo root), so it works correctly regardless of the working directory it's launched from.

**Input validation:** All tool inputs use Pydantic v2 `BaseModel` subclasses with `ConfigDict(str_strip_whitespace=True, extra='forbid')`. This prevents extra parameters from silently being ignored and strips whitespace from all string inputs automatically.

### 5.4 11 Exposed MCP Tools

#### `nova_shard_interact`
**Purpose:** Load one or more shards into context for synthesis.  
**Behavior:**
- If no shard IDs are provided, calls `guess_relevant_shards()` to auto-select up to 3 relevant shards using fuzzy token overlap + confidence weighting.
- On load, boosts the accessed shard's confidence by +0.05 (capped at 1.0) — emulating how accessing a memory strengthens it.
- Returns fragments (last N conversation turns), metadata, confidence, tags, and context summary.
- Every session should start with this tool.

#### `nova_shard_create`
**Purpose:** Create a new shard.  
**Behavior:**
- Sanitizes the name to `<theme>_<intent>` (lowercase, alphanumeric + underscores, max 40 chars).
- Registers the shard in the knowledge graph as an entity.
- Optionally links to `related_shards` immediately via `add_relation()`.
- Triggers `enrich_shard_async()` post-write hook to generate local embeddings and topic tags.
- Runs `find_merge_candidates()` and returns suggestions if similar shards exist (cosine > 0.85).

#### `nova_shard_update`
**Purpose:** Append a conversation turn to an existing shard.  
**Behavior:**
- Appends `{timestamp, user_message, ai_response}` to `conversation_history`.
- Calls `maybe_compact_shard()` — triggers auto-compaction if turn count exceeds threshold.
- Triggers `enrich_shard_async()` post-write hook.
- Syncs the shard's confidence to the knowledge graph entity.

#### `nova_shard_search`
**Purpose:** Search shards by keyword with confidence weighting.  
**Algorithm:**
```
base_score = len(query_tokens ∩ searchable_tokens) / len(query_tokens)
weighted_score = base_score * confidence
```
- Searches against: guiding question, context summary, topic tags, theme, intent, shard_id.
- Excludes archived, forgotten, and (by default) low-confidence (`< 0.4`) shards.
- Returns top N results sorted by weighted score.

#### `nova_shard_list`
**Purpose:** List all shards sorted by confidence descending.  
**Returns:** shard_id, guiding_question, tags, theme, intent, confidence, usage_count for every shard.

#### `nova_shard_merge`
**Purpose:** Merge multiple related shards into a single meta-shard.  
**Behavior:**
- Combines all `conversation_history` entries, sorted by timestamp.
- Creates new meta-shard with `intent: meta_synthesis`.
- Optionally soft-archives the source shards.
- Wires graph relations: each source shard `extends` the new meta-shard.
- New shard gets `"merged_from"` and `"source_questions"` in meta_tags for provenance.

#### `nova_shard_archive`
**Purpose:** Soft-archive a shard.  
**Behavior:** Sets `intent = "archived"` + `archived_at` timestamp. Excluded from search and interact. Content preserved on disk. Represents deprioritization, not deletion.

#### `nova_shard_forget`
**Purpose:** Hard soft-delete with provenance log.  
**Distinction from archive:** Forgotten shards are intentionally excluded from all operations. The forget reason is logged to the usage file. Content stays on disk for audit. Confidence set to 0.0. This represents a deliberate act of removing a shard from active memory while documenting why.

#### `nova_shard_consolidate`
**Purpose:** Run the full maintenance cycle.  
**Three-phase process:**
1. **Decay phase** — Applies confidence decay to all non-forgotten shards not accessed in `DECAY_INTERVAL_DAYS`. Formula: `MAX(0.1, confidence * 0.95)` per period.
2. **Compaction phase** — Auto-compacts any shard exceeding `COMPACT_THRESHOLD` turns.
3. **Merge suggestion phase** — For all enriched shards, computes pairwise cosine similarity. Returns up to 10 candidate pairs above the merge threshold.

Recommended cadence: run every 3 sprints.

#### `nova_graph_query`
**Purpose:** Query the inter-shard knowledge graph by pattern.  
**Parameters:** `source`, `target`, `relation_type` (all optional — omit for all relations).  
**Returns:** Matching relations enriched with source/target guiding questions.

#### `nova_graph_relate`
**Purpose:** Manually add a directed relation between two shards.  
**Relation types:**
- `influences` — shard A shapes the thinking in shard B
- `depends_on` — shard A requires shard B to make sense
- `contradicts` — shards are in tension, revisit both
- `extends` — shard A builds on shard B
- `references` — shard A cites shard B

Duplicate relations (same source + target + type) are silently ignored.

### 5.5 Confidence and Decay System

The confidence system is the core mechanism that gives the memory its **temporal relevance** property.

**Decay formula:**
```python
periods = days_since_last_used // DECAY_INTERVAL_DAYS
new_confidence = confidence
for _ in range(periods):
    new_confidence = MAX(0.1, new_confidence * (1 - DECAY_RATE))
```

With defaults (`DECAY_RATE=0.05`, `DECAY_INTERVAL_DAYS=7`):
- After 1 week without access: `confidence *= 0.95`
- After 1 month: `confidence ≈ 0.81`
- After 3 months: `confidence ≈ 0.54`
- After 6 months: `confidence ≈ 0.29` (crosses "low confidence" threshold of 0.4)
- Floor at `0.1` — never completely disappears

**Boost on access:** Every time a shard is loaded via `nova_shard_interact`, its confidence is boosted by `+0.05` (capped at 1.0). This mimics memory consolidation — accessed memories become stronger.

**Effect on search:** `weighted_score = base_score * confidence`  
A shard with perfect keyword overlap (`base_score = 1.0`) but stale confidence of 0.3 will rank below a shard with partial overlap (`base_score = 0.6`) but fresh confidence of 1.0 (`weighted = 0.6` vs `0.3`). Recently accessed knowledge naturally surfaces first.

**Low-confidence tag:** Shards below 0.4 confidence get tagged `low_confidence` and are **excluded from default search and interact operations**. They remain accessible explicitly via `include_low_confidence=True`.

### 5.6 Auto-Compaction Engine

Shards grow as conversations accumulate. At `COMPACT_THRESHOLD` turns (default: 30), the compaction engine fires automatically on write:

**Process:**
1. Splits history into `older_turns[:-15]` and `recent_turns[-15:]`
2. Calls `_generate_compaction_summary(older_turns, shard_id)` to summarize the removed content
3. Prepends summary to `context.summary` with a turn count annotation
4. Replaces `conversation_history` with only the recent turns
5. Records `last_compacted` timestamp and cumulative `compacted_turn_count`

**Compaction summary content (local, no API):**
The `nova_embeddings_local.py` version uses a heuristic approach: it takes the first user message, the middle user message, and the last user message as "anchors" and formats them as `"Conversation covering: [first] → [middle] → [last] (N turns compacted)"`. This is intentionally approximate — the goal is to leave a breadcrumb, not a perfect summary.

The `context_extractor.py` (OpenAI-based enrichment) generates proper GPT-4 summaries when available.

### 5.7 Knowledge Graph Layer

The knowledge graph lives in `shard_graph.json` with the following schema:

```json
{
  "entities": {
    "<shard_id>": {
      "type": "Shard",
      "guiding_question": "string",
      "theme": "string",
      "intent": "string",
      "created_at": "ISO 8601",
      "confidence": 1.0
    }
  },
  "relations": [
    {
      "source": "<shard_id>",
      "target": "<shard_id>",
      "type": "influences | depends_on | contradicts | extends | references",
      "notes": "string",
      "created_at": "ISO 8601"
    }
  ]
}
```

**Auto-wired relations:**
- On `nova_shard_create`: if `related_shards` are provided, relations are automatically created with the specified `relation_type`.
- On `nova_shard_merge`: each source shard automatically gets an `extends` relation to the new meta-shard.

**Query pattern:** The `query_graph()` function performs simple dictionary key matching over the relations list. Pattern keys are all optional; omitting a key matches all values. No graph traversal engine — this is intentionally simple at current scale.

**Graph confidence sync:** On every `nova_shard_update`, the graph entity's `confidence` field is updated to match the shard's current confidence, keeping the graph and shards in sync.

### 5.8 Local Embedding Module

`mcp/nova_embeddings_local.py` is the offline embedding backend, replacing what was originally an OpenAI API call.

**Model:** `all-MiniLM-L6-v2` from sentence-transformers  
- 80MB download (first run only, cached to `~/.cache/huggingface/`)  
- 384-dimensional embeddings  
- CPU-only, no GPU required  
- Apache 2.0 license  
- Semantically meaningful similarity scores

**Lazy loading:** The model is loaded once at first use (session-scoped global `_embedding_model`), not at server startup. If `sentence-transformers` is not installed, enrichment gracefully degrades to `enrichment_status = "pending_no_model"` — keyword-only search still works.

**What gets embedded:**
```python
embed_text = guiding_question + " " + " ".join([
    turn.get("user", "") + " " + turn.get("ai", "")
    for turn in recent_history[-5:]
])
```
The last 5 conversation turns are concatenated with the guiding question to form the embedding input. This captures both the shard's topic and its recent conversational context.

**Topic extraction (no LLM):**
```python
words = re.findall(r'\b[a-zA-Z]{4,}\b', embed_text.lower())
keywords = [w for w in words if w not in stopwords][:6]
```
Simple 4-character minimum word filter with a hand-curated stopword list extracts 6 topic keywords per shard. Fast and deterministic.

### 5.9 Usage Tracking

Every tool operation appends a JSON line to `nova_usage.jsonl`:

```json
{"timestamp": "ISO 8601", "tool": "nova_shard_search", "shards": [], "metadata": {"query": "AI memory"}}
```

This provides a full audit trail of which tools were called, which shards were involved, and when. The `nova://usage` MCP resource exposes the last 100 entries for inspection.

**Operations tracked:** `nova_shard_interact`, `nova_shard_create`, `nova_shard_update`, `nova_shard_search`, `nova_shard_consolidate`, `nova_shard_forget`.

### 5.10 MCP Resources (Read-only endpoints)

In addition to tools, the server exposes 4 MCP resources (queryable via `nova://` URI scheme):

| Resource | URI | Content |
|---|---|---|
| Skill definition | `nova://skill` | Contents of `SKILL_v2.md` (falls back to `SKILL.md`) |
| Shard index | `nova://index` | Full `shard_index.json` as JSON |
| Knowledge graph | `nova://graph` | Full `shard_graph.json` as JSON |
| Usage log | `nova://usage` | Last 100 JSONL entries from `nova_usage.jsonl` |

---

## 6. Forgemaster Orchestration Layer

### 6.1 System Overview

Forgemaster is the execution layer that sits above NOVA. It transforms high-level design documents or feature requests into parallelizable, model-routed work units ("tickets"), executes them with appropriate AI agents, and writes results back to NOVA.

The `forgemaster/AGENTS.md` file defines the system with a YAML frontmatter block:

```yaml
name: forgemaster
agent_roles: [orchestrator, implementer, reviewer, researcher]
preferred_models:
  claude-sonnet: [architecture, review, ambiguity]
  gemini-flash: [implementation, boilerplate, structured-output]
  gpt-4o: [research, documentation]
tools_allowed: [bash, git, mcp_nova]
max_context_usage: 0.5
verification_priority: high
```

### 6.2 Model Routing Strategy

The orchestrator classifies each ticket into a type and routes it to the optimal model:

| Task Type | Characteristics | Model |
|---|---|---|
| `architecture` | Ambiguous, cross-cutting, requires judgment | claude-sonnet |
| `review` | Quality gate, spec compliance check | claude-sonnet |
| `ambiguity` | Underspecified requirements | claude-sonnet |
| `implementation` | Clear spec, 1–3 files, bounded scope | gemini-flash |
| `boilerplate` | Repetitive structure, templated | gemini-flash |
| `structured-output` | Schema-defined JSON/YAML/config | gemini-flash |
| `research` | Broad knowledge, documentation synthesis | gpt-4o |
| `documentation` | Writing, README, ADR authoring | gpt-4o |

**Hard routing rule:** Ambiguous tickets MUST NOT go to gemini-flash. Unresolved ambiguity must be resolved by claude-sonnet first. This is a hard gate in the orchestrator skill.

### 6.3 Core Skill Library (9 skills)

All core skills live in `forgemaster/skills/` and are markdown files that define agent behavior when loaded as context.

#### `forgemaster-orchestrator.md`
The entry point for every sprint. Defines the sprint start protocol (load NOVA context → read design doc → decompose into typed tickets → assign models). Specifies the exact ticket format every ticket must follow, including: type, model, title, depends_on, context_shards, spec, and acceptance_criteria. Enforces maximum 5 tickets per sprint wave.

#### `forgemaster-parallel-lanes.md`
Governs concurrent execution. Defines three conditions for parallelism eligibility: no mutual output dependencies, no shared file writes, no unresolved architecture ticket blocking both. Each lane receives an exact "Lane Context" package: ticket spec, skill to load, pre-loaded NOVA shard content, and constraints. Defines wave completion criteria: all lanes DONE, all acceptance criteria verifiable, no unresolved escalations.

#### `forgemaster-writing-plans.md`
For decomposing design documents into tickets. Handles the zero-to-ticket transformation before the orchestrator can route.

#### `forgemaster-implementation.md`
Single-ticket execution skill. Governs how an agent executes a bounded, fully-specified implementation ticket.

#### `forgemaster-systematic-debugging.md`
Root cause investigation protocol. Mandates hypothesis-evidence-fix cycles. Prohibits patching symptoms without understanding root cause.

#### `forgemaster-verification.md`
Evidence-before-completion protocol. An agent cannot claim a ticket is DONE without satisfying verifiable acceptance criteria. This is the anti-hallucination gate.

#### `forgemaster-git-workflow.md`
Branch setup, commit conventions, integration, and PR creation protocols.

#### `forgemaster-code-review.md`
Two-stage review: spec compliance first (does the code do what the ticket specified?), then quality (is it idiomatic, maintainable, secure?). Stage 1 gates Stage 2 — quality review on non-compliant code is skipped.

#### `forgemaster-nova-session-handoff.md`
The session boundary protocol. Defines exactly what to write to NOVA before ending any session:
```
CURRENT STATE: [branch, last completed ticket, test status]
IN PROGRESS: [what was started, what remains]
DECISIONS MADE: [key architectural choices and why]
NEXT ACTION: [exactly what to do first next session]
```
Without this, the next session starts from zero context.

### 6.4 Agent Persona System

`forgemaster/agents/` contains **357 agent persona definitions** across **18 domain folders**:

| Domain | Purpose |
|---|---|
| `academic/` | Research, scholarly writing |
| `autonomous-agents/` | Browser automation, lead gen, data collection |
| `design/` | UI/UX, design systems |
| `engineering/` | Software development across stacks |
| `game-development/` | Full game studio pipeline |
| `integrations/` | API integrations, connector agents |
| `marketing/` | Content, campaign, copywriting |
| `paid-media/` | Media buying, campaign management |
| `product/` | Product management, roadmap |
| `project-management/` | Jira, Linear, Notion |
| `research/` | Market research, competitive analysis |
| `sales/` | CRM, lead qualification |
| `spatial-computing/` | AR/VR, spatial interfaces |
| `specialized/` | Domain-specific one-off agents |
| `strategy/` | Business strategy, consulting |
| `support/` | Customer support automation |
| `testing/` | QA, test automation |
| `examples/` | Reference implementations |

Each agent file uses standard YAML frontmatter: name, role, division, tier (`specialist | orchestrator | reviewer | researcher`), `model_preference`, description, emoji, color (hex), and 2–5 tags.

### 6.5 Extended Skill Library (150+ skills)

`forgemaster/library/` contains **208 SKILL.md files** across **13 domain categories**, sourced from external repos and copied in (no external dependencies):

| Category | Key Skills |
|---|---|
| **Agentic Workflows** (20) | Brainstorming, TDD, parallel agents, plan execution, context preservation, error recovery, batch ledger, quality monitor |
| **Engineering** (12) | Python, TypeScript, Go, Rust, React, Next.js, GraphQL, OpenAPI, WebAssembly, Shell, Regex, CSS |
| **Infrastructure** (8+) | Docker, Kubernetes, Helm, Terraform, Ansible, CI/CD, AWS, Azure, GCP |
| **Data/AI/ML** | ML engineering, LLM fine-tuning, Hugging Face, data pipelines |
| **Databases** | PostgreSQL, MongoDB, Redis, Elasticsearch, SQL |
| **Frontend/Design** | Impeccable (20 refinement skills), UI/UX Pro Max, design systems |
| **Security** | OWASP audit, OAuth, cryptography, compliance |
| **Game Dev** | Full studio pipeline (35 skills — Claude-Code-Game-Studios) |
| **Project Management** | Jira, Linear, Notion, Confluence, Agile |
| **Code Intelligence** | GitNexus (impact analysis, PR review, refactoring) |
| **Autonomous Agents** | Browser, Researcher, Collector, Lead Gen, Predictor, Trader |
| **Communication** | Technical writing, email, presentations, PDF |
| **Observability** | Prometheus, Sentry, Slack |

All skill files follow the content standard defined in `forgemaster/STANDARDS.md`: required YAML frontmatter (name, version, description, tags, domain, author, type) and required body sections (Overview, When to Use, Capabilities, Procedure, Examples, References). Max 300 lines per skill file; hard gates use fenced `<HARD-GATE>` blocks.

### 6.6 Sprint Workflow

```
1. nova_shard_interact("project name current state")
   └── Load project context shards from NOVA

2. Read design doc / feature request
   └── Use forgemaster-writing-plans skill

3. Classify tickets by type
   └── Use forgemaster-orchestrator skill

4. Dispatch lanes
   └── Use forgemaster-parallel-lanes skill
       ├── Each lane: ticket + skill + NOVA shard context + constraints
       └── Lanes execute concurrently for independent tickets

5. Review results
   └── Use forgemaster-code-review skill
       ├── Stage 1: Spec compliance
       └── Stage 2: Code quality

6. Write decisions back to NOVA
   └── nova_shard_update(shard_id, user_message, ai_response)

7. Every 3 sprints: nova_shard_consolidate()
   └── Decay stale shards, compact large shards, surface merge candidates
```

### 6.7 Forgemaster Content Standards

`forgemaster/STANDARDS.md` documents the full content audit:

| Directory | File Count | Purpose |
|---|---|---|
| `agents/` | 357 | Agent persona definitions |
| `library/` | 208 | Reusable SKILL.md files |
| `slash-commands/` | 84 | Claude slash-command prompts |
| `workflows/` | 56 | Step-by-step workflow processes |
| `templates/` | 60 | Project scaffolding and planning templates |
| `rules/` | 20 | Coding standards and hooks config |
| `docs/` | 18 | Reference indexes and architecture notes |
| **TOTAL** | **803** | All Forgemaster content files |

---

## 7. Python Utilities Layer

### 7.1 shard_index.py

**Location:** `python/shard_index.py`  
**Dual purpose:** Standalone maintenance script AND importable module for nova_server_v2.

**Key functions:**

| Function | Purpose |
|---|---|
| `build_index()` | Scan `shards/` directory, parse all JSONs, build complete dict-based index |
| `update_index()` | Call `build_index()`, save to `shard_index.json`, return result |
| `load_index()` | Read `shard_index.json` from disk, migrate legacy list-based format if needed |
| `classify_tags()` | Generate `recent`, `stale`, `frequently_used`, `archived`, `enriched` status tags |
| `_migrate_legacy_index()` | Convert old list-based index format to current dict-based format |

**Index entry schema:**
```json
{
  "<shard_id>": {
    "shard_id": "string",
    "filename": "*.json",
    "guiding_question": "string",
    "tags": ["recent", "enriched"],
    "meta": {"intent": "...", "theme": "...", "usage_count": 0, "last_used": "..."},
    "context_summary": "string",
    "context_topics": ["tag1", "tag2"],
    "confidence": 1.0
  }
}
```

Run standalone: `python python/shard_index.py` — rebuilds index and reports count.

### 7.2 context_extractor.py

**Location:** `python/context_extractor.py`  
**Purpose:** Batch semantic enrichment for all shards using OpenAI GPT-4 + ada-002 embeddings.  
**Requires:** `OPENAI_API_KEY` in `.env` (unlike the MCP server which is API-free).

This is the **high-quality enrichment path** vs the local embedding path in `nova_embeddings_local.py`:
- `context_extractor.py` → GPT-4 generated summaries + ada-002 embeddings (high quality, costs money)
- `nova_embeddings_local.py` → Heuristic summaries + all-MiniLM-L6-v2 embeddings (zero cost, offline)

**Usage:**
```bash
python context_extractor.py          # Enrich all un-enriched shards
python context_extractor.py --force  # Re-enrich all shards (even already enriched)
```

**Process per shard:**
1. Skip if already has `context.embedding` (unless `--force`)
2. Serialize full shard JSON → truncate to 12,000 chars
3. Call GPT-4 to generate `{summary, topics, conversation_type}` JSON
4. Call ada-002 to generate embedding of `summary + topics`
5. Write `context` field back to shard JSON
6. 1-second sleep between shards (rate limiting)
7. Update index after all shards processed

**Fallback parsing:** If GPT-4 returns non-JSON (e.g., wraps in markdown fences), a regex-based `_fallback_parse()` attempts to extract the fields from free text.

### 7.3 Supporting Scripts

| Script | Purpose |
|---|---|
| `python/rename_shards.py` | Batch rename shard files, updating `shard_id` fields consistently |
| `python/dedup_json.py` | Detect and handle duplicate shard content |
| `python/main.py` | Main entry point (likely the original pre-MCP exploration script) |

---

## 8. Migration Tooling

### chatgpt_to_nova.py

**Location:** `tools/chatgpt_to_nova.py`  
**Purpose:** One-time migration utility that converts ChatGPT conversation exports (ZIP → JSON) into NOVA shard format.

**Usage:**
```bash
python tools/chatgpt_to_nova.py \
  --input "E:/ChatGPT Chats" \
  --output "./shards" \
  --min-turns 2 \
  --dry-run    # Preview without writing
```

**Key capabilities:**
- Reads `conversations-000.json`, `conversations-001.json`, etc. from ChatGPT export ZIP
- Follows the ChatGPT conversation tree (conversations are stored as branching trees, not linear lists) by walking backwards from `current_node` to root
- Skips conversations with fewer than `--min-turns` turns (default: 2) to avoid noise
- Derives a title from the conversation's title field, or falls back to the first 60 chars of the first user message
- Generates a `guiding_question` in the format: `"What was discussed in: <title>?"`
- Converts ChatGPT `{role: user/assistant, content}` format to NOVA `{timestamp, user, ai}` format
- Names output files in the pattern `chatgpt_<theme>_<sanitized_title>.json`
- This tool was used to create all 424 shards currently in the repo

---

## 9. Live Shard Data Analysis

### Scale
- **424 JSON shards** in `shards/`
- All follow the `chatgpt_ai_ml_*.json` naming pattern — indicating they were migrated from a ChatGPT export focused heavily on AI/ML topics
- The `chatgpt_ai_ml_` prefix comes from the migration tool's default theme detection

### Content Character
The shards represent real personal intellectual history. A sample from `chatgpt_ai_ml_ai_memory_breakthrough.json` reveals deep conversations about:
- The NOVA system's own origin and rationale
- Comparisons between the author's predictions and Sam Altman's public statements about AI memory
- Discussion of neural architecture for AI cognition
- Philosophy of intelligence as structured data access

This is not demo data — these are actual conversations that contain the intellectual lineage of the NOVA concept itself. The system is therefore **self-referential**: its memory contains the thinking that led to its own creation.

### Shard Structure (observed from sample)
- Long multi-turn conversations (11+ turns in sample)
- Mix of user speculation/claims and detailed AI responses with structured formatting
- Some shards contain ASCII diagrams, tables, and multi-paragraph analyses
- Timestamps from late 2025 (ChatGPT export period)
- Most shards lack the `context` field — they have not yet been enriched with embeddings (likely migration was recent)

### Naming Convention
```
chatgpt_ai_ml_<kebab_topic>.json
```
Examples:
- `chatgpt_ai_ml_ai_memory_breakthrough.json`
- `chatgpt_ai_ml_agi_and_nuclear_race.json`
- `chatgpt_ai_ml_ai_trust_vs_power.json`
- `chatgpt_ai_ml_3d_printed_suicide_drones.json` (range of topics covered)

---

## 10. Runtime Files and Configuration

### Files Generated at Runtime (Never Commit)

| File | Type | Content |
|---|---|---|
| `shard_index.json` | JSON | Full index of all shards with metadata, tags, confidence |
| `shard_graph.json` | JSON | Knowledge graph: entities + directed relations |
| `nova_usage.jsonl` | JSONL, append-only | One line per tool operation: timestamp, tool, shard_ids, metadata |

### `.env` (Never Commit)
```ini
OPENAI_API_KEY=sk-...    # Required for context_extractor.py only
                          # NOT required for nova_server_v2.py
```

### Claude Desktop Integration
```json
{
  "mcpServers": {
    "nova": {
      "command": "python",
      "args": ["C:/Users/Moldo/Master Project NOVA/repos/forgemaster-harvest/NOVA-Cognition-Framework/mcp/nova_server_v2.py"],
      "env": {
        "NOVA_SHARD_DIR": "C:/Users/Moldo/Master Project NOVA/repos/forgemaster-harvest/NOVA-Cognition-Framework/shards"
      }
    }
  }
}
```

The MCP client (Claude Desktop, Claude Code, Cursor) connects via stdio and the tools are exposed as callable functions with the `nova_` prefix.

---

## 11. Dependency Analysis

### MCP Server (`mcp/requirements.txt`)
```
mcp[cli]>=1.0.0          # MCP SDK with FastMCP framework
pydantic>=2.0.0          # Input validation for all tool parameters
sentence-transformers>=2.2.0  # all-MiniLM-L6-v2 local embeddings
python-dotenv>=1.0.0     # .env file loading
```

**Zero hard cloud dependencies** for the MCP server. All embeddings are local. No OpenAI API key required to run the memory system.

### Python Utilities (`python/requirements.txt`)
Likely includes: `openai`, `python-dotenv` (for `context_extractor.py` which uses GPT-4 + ada-002)

### Import relationships
```
nova_server_v2.py
    └── imports nova_embeddings_local.py (enrich_shard_async, _generate_compaction_summary)
    └── (previously imported shard_index.py, now has its own copy of the functions)

shard_index.py
    └── standalone, no internal imports

context_extractor.py
    └── imports shard_index.py (SHARD_DIR, update_index)
    └── imports openai
    └── imports dotenv
```

---

## 12. System Integration Map

```
MCP Client (Claude Desktop / Claude Code / Cursor)
    │
    │ stdio (MCP protocol)
    │
    ▼
nova_server_v2.py
    │
    ├── reads/writes ──→ shards/*.json (flat file storage)
    ├── reads/writes ──→ shard_index.json (search index)
    ├── reads/writes ──→ shard_graph.json (knowledge graph)
    ├── appends     ──→ nova_usage.jsonl (audit log)
    └── imports     ──→ nova_embeddings_local.py
                            └── sentence-transformers
                                └── all-MiniLM-L6-v2
                                    (downloaded once to ~/.cache/huggingface/)

python/context_extractor.py (standalone enrichment, optional)
    ├── reads/writes ──→ shards/*.json
    ├── calls        ──→ OpenAI GPT-4 (summary generation)
    ├── calls        ──→ OpenAI ada-002 (embedding generation)
    └── calls        ──→ shard_index.py (update_index)

tools/chatgpt_to_nova.py (one-time migration, standalone)
    └── reads        ──→ ChatGPT export JSON files
    └── writes       ──→ shards/*.json (new shard creation)

Forgemaster skills/agents (context files, loaded by MCP client)
    └── read by     ──→ AI agent as instruction context
    └── reference   ──→ nova_* tools (via MCP client)
```

---

## 13. Design Patterns and Architectural Decisions

### Pattern 1: Filesystem as Database
**Decision:** Use flat JSON files instead of a vector database or SQL.  
**Rationale:** Zero infrastructure dependency. Every shard is human-readable and directly auditable. No schema migrations. No connection management. No Docker required to run a dev instance.  
**Trade-off:** Scales poorly beyond ~10,000 shards (full-scan search). Acceptable for personal use.

### Pattern 2: Fuzzy + Weighted Retrieval (No Full Vector Search)
**Decision:** Token overlap scoring weighted by confidence, with cosine similarity only for merge suggestions (not primary search).  
**Rationale:** Fast, deterministic, requires no vector index to maintain. Cosine search is still available for enriched shards via `find_merge_candidates()`.  
**Trade-off:** Search quality lower than pure semantic search for concept-level similarity.

### Pattern 3: Confidence as Temporal Relevance Proxy
**Decision:** Float confidence score that decays with disuse and boosts on access.  
**Rationale:** Eliminates need for explicit archiving of old but not-yet-decided-obsolete content. The system self-organizes around what's actively being used.  
**Trade-off:** Requires periodic `nova_shard_consolidate()` to apply decay. Decay doesn't run continuously — it runs on consolidation or at access time if triggered.

### Pattern 4: Hard Soft-Delete (forget ≠ delete)
**Decision:** Two levels of "removal" — archive (deprioritize) and forget (intentionally exclude) — neither deleting the file.  
**Rationale:** Preserves auditability. Forgotten shards remain on disk for provenance review. Prevents accidental data loss. Aligns with the thesis that memory is never truly deleted, just de-accessed.

### Pattern 5: Guiding Question as Shard Anchor
**Decision:** Every shard has a single guiding question that defines its scope.  
**Rationale:** Forces intentional scoping during creation. Acts as the primary retrieval anchor. Avoids "junk drawer" shards with no clear purpose.

### Pattern 6: Post-Write Hooks
**Decision:** Enrichment (embedding generation) happens synchronously in `enrich_shard_async()` as a post-write hook, despite the "async" name.  
**Observation:** The function is named `enrich_shard_async` but is called synchronously (no `await`). The "async" in the name reflects design intent (to be run async in a future refactor) but the current implementation is blocking.

### Pattern 7: v1/v2 Co-existence with Clear Active Flag
**Decision:** Keep `nova_server.py` (v1) and `SKILL.md` (v1) in the repo alongside v2, labeled clearly as reference-only.  
**Rationale:** Preserves architectural lineage without polluting the active codebase. CLAUDE.md explicitly warns "never use v1."

### Pattern 8: Skill-as-Context-Injection
**Decision:** Forgemaster skills are markdown files read at runtime and injected into the LLM context.  
**Rationale:** No code compilation or deployment needed to update agent behavior. Skills are version-controlled, human-readable, and can be exchanged per operation type.

---

## 14. Strengths and Limitations

### Strengths

1. **Zero infrastructure footprint** — Runs with `python nova_server_v2.py`. No database, no Docker, no cloud services required for core operation.

2. **Offline-capable** — Local embeddings mean the memory system works with no internet connection after initial model download.

3. **Self-organizing memory** — Confidence decay ensures the active working set naturally surfaces without manual curation.

4. **Complete auditability** — Every shard is a readable JSON file. Every operation is logged to `nova_usage.jsonl`. Nothing is silently deleted.

5. **Semantic + structural retrieval** — Both keyword overlap (fast, deterministic, always available) and cosine similarity (available for enriched shards) are supported.

6. **Human-in-the-loop by design** — Forgemaster's skill definitions explicitly require human approval at design doc creation, plan approval, and PR review stages. Agents can't autonomously ship.

7. **Modular skill system** — Agent behavior is defined as swappable markdown files. Adding or modifying a skill requires no code change.

8. **Real, populated memory** — 424 shards of actual thinking migrated from ChatGPT provides immediate utility without a cold-start problem.

### Limitations

1. **No concurrent write safety** — Multiple simultaneous writes to the same shard file are not protected by a lock. Race conditions are possible if tools are called concurrently (unlikely in practice with single-user MCP, but worth noting).

2. **Search scales linearly** — `nova_shard_search` scans all index entries on every call. At 424 shards this is fast; at 100,000+ it would degrade significantly.

3. **`enrich_shard_async` is synchronous** — Despite the name, enrichment blocks the write response. For shards with long conversation histories, this could cause noticeable latency.

4. **Compaction summary quality** — The local (no-API) compaction summary is heuristic-based and loses nuance. The GPT-4-based path in `context_extractor.py` is significantly better but requires spending money.

5. **No cross-shard query language** — The knowledge graph query is pattern-based (exact key matching), not a traversal engine. There's no way to ask "show me all shards that transitively depend on shard X."

6. **shard_index.json as soft cache** — The index is rebuilt from scratch on many operations (`update_index()` is called on every create/update/merge/archive). For 424 shards this is fast (~10ms). For 10,000+ shards, this would become a bottleneck.

7. **No authentication on MCP endpoints** — The server trusts all callers. This is acceptable for a local personal tool but would be a security concern if exposed to a network.

8. **Forgemaster is procedural, not programmatic** — The orchestration layer exists as markdown skill files that instruct LLMs. It requires a capable LLM to follow the protocols. There's no programmatic enforcement of routing rules — only the LLM's ability to follow instructions.

---

## 15. Key Observations

1. **The repo is self-referential.** The 424 shards include conversations analyzing the very thesis that motivates NOVA's design. The system literally contains its own origin story as memory. Shard `chatgpt_ai_ml_ai_memory_breakthrough.json` is a dated record of the author predicting that AI memory, not reasoning, would be the next breakthrough — before Altman's public 2025 statement.

2. **This is a one-person AGI scaffold.** The combination of persistent memory (NOVA), multi-model routing (Forgemaster), 357 agent personas, and 150+ domain skills represents a single developer's attempt to build a persistent, context-aware AI system that accumulates knowledge across time. The philosophical underpinning ("structure over processing power") is not just design philosophy — it's a testable claim the author is actively pursuing.

3. **The v1→v2 evolution is visible.** The repo retains `nova_server.py`, `SKILL.md`, and the original design docs. Reading the diff between v1 (7 tools, no graph, no decay) and v2 (11 tools, knowledge graph, confidence decay, auto-compaction, local embeddings) tells the story of the system's evolution in a single commit history.

4. **The content standard is production-grade.** `forgemaster/STANDARDS.md` enforces YAML frontmatter schemas, required body sections, max file sizes, and visual hard gates. With 803 content files, this level of standardization is not optional — it's necessary for the skill system to be discoverable and machine-navigable.

5. **The migration tooling is the onboarding path.** New users of NOVA would start with `chatgpt_to_nova.py` to seed their memory system from existing conversation history. This lowers the cold-start cost dramatically — you don't need to manually create shards from scratch.

6. **Local-first philosophy is a deliberate stance.** Moving from OpenAI ada-002 to `all-MiniLM-L6-v2` was not a cost-saving measure — it reinforced the "zero cloud dependency" principle for the core memory operations. The OpenAI path is preserved in `context_extractor.py` for users who want higher-quality enrichment and are willing to pay for it.

7. **The knowledge graph is aspirationally underused.** With 424 shards and only manual `nova_graph_relate` calls to wire relations (plus automatic ones from merge), the graph is likely sparse at this stage. The system is designed for graph navigation to become increasingly valuable as the user deliberately documents inter-shard relationships over time.

---

*Generated: March 26, 2026 | Repository: NOVA-Cognition-Framework | Analysis covers all files visible as of generation date*
