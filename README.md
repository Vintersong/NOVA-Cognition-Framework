# NOVA-Cognition-Framework

A unified repository containing **NOVA** (persistent AI memory) and **Forgemaster** (multi-agent orchestration). NOVA is the memory layer. Forgemaster is the execution layer. They share one repo and one data store.

---

## What is NOVA?

NOVA is a persistent memory system for AI agents. It stores conversations and decisions as modular JSON "shards" — each with metadata, confidence scores, and decay rates. A knowledge graph tracks relationships between shards so agents can retrieve contextually relevant memory, not just recent history.

NOVA runs as an MCP server, meaning any MCP-compatible client (Claude Desktop, Claude Code, Cursor) can connect to it and query/write memory using structured tool calls. No external API key is required — embeddings are generated locally via `sentence-transformers`.

**Key capabilities:**
- Store and retrieve memory shards with local semantic search (no API key needed)
- Confidence-weighted recall with time-based decay
- Automatic shard compaction and consolidation
- Knowledge graph of inter-shard relationships
- Post-write enrichment hooks for automatic embedding generation

---

## What is Forgemaster?

Forgemaster is a multi-agent orchestration layer built on top of NOVA. It decomposes tasks into typed tickets and routes each to the optimal model — Claude Sonnet for architecture and review, Gemini Flash for implementation and boilerplate, Claude Haiku for research and documentation.

Agents run in parallel sandboxed lanes and return results as PRs for human review. NOVA is Forgemaster's memory backplane: every sprint reads project context from shards and writes decisions back.

**Key capabilities:**
- Task decomposition from design docs
- Model routing by task type
- Parallel sandboxed execution lanes
- Human-in-the-loop at design, plan, and PR stages
- Full NOVA integration for persistent project context

---

## How They Connect

```
You (design doc / feature request)
        │
        ▼
  Forgemaster Orchestrator
  ├── loads NOVA shards (project context)
  ├── decomposes into typed tickets
  ├── routes tickets to optimal models
  │       ├── claude-sonnet  → architecture, review
  │       ├── gemini-flash   → implementation, boilerplate
  │       └── claude-haiku   → research, documentation
  ├── agents execute in parallel lanes
  ├── results returned as PRs
  └── decisions written back to NOVA shards
```

---

## Quick Start

### 1. Install dependencies

```bash
cd mcp/
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# No API key required — NOVA uses local embeddings
```

### 3. Run the NOVA MCP server

```bash
python mcp/nova_server_v2.py
```

### 4. Connect a client

Add to your MCP client config (e.g. Claude Desktop `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "nova": {
      "command": "python",
      "args": ["path/to/mcp/nova_server_v2.py"],
      "env": {
        "NOVA_SHARD_DIR": "path/to/shards"
      }
    }
  }
}
```

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `NOVA_SHARD_DIR` | `shards/` | Path to shard JSON files |
| `NOVA_COMPACT_THRESHOLD` | `30` | Conversation turns before auto-compaction |
| `NOVA_DECAY_RATE` | `0.05` | Confidence decay per 7-day period |
| `NOVA_MERGE_THRESHOLD` | `0.85` | Cosine similarity threshold for merge suggestions |

---

## Directory Structure

```
NOVA-Cognition-Framework/
  mcp/
    nova_server_v2.py         ← active MCP server (12 tools)
    nova_embeddings_local.py  ← local sentence-transformer embeddings
    SKILL_v2.md               ← active cognitive instructions
    requirements.txt
    _deprecated/              ← v1 reference files (do not use)
  python/
    shard_index.py            ← index management (imported by v2 server)
    context_extractor.py      ← batch semantic enrichment utility
    dedup_json.py             ← deduplication utility
    rename_shards.py          ← shard rename utility
    main.py
  shards/                     ← live shard data (never edit manually)
  forgemaster/
    AGENTS.md                 ← global agent config and routing rules
    SKILL_LIBRARY.md          ← skill index
    STANDARDS.md              ← coding and review standards
    skills/
      forgemaster-orchestrator.md
      forgemaster-parallel-lanes.md
      forgemaster-writing-plans.md
      forgemaster-implementation.md
      forgemaster-systematic-debugging.md
      forgemaster-verification.md
      forgemaster-git-workflow.md
      forgemaster-code-review.md
      forgemaster-qa-review.md
      forgemaster-nova-session-handoff.md
  tools/
    chatgpt_to_nova.py        ← ChatGPT export → NOVA shard migration
    autoresearch.py           ← local LLM research loop → NOVA shards
  docs/                       ← reference docs and architecture notes
  game/                       ← standalone web app (index.html)
  shard_index.json            ← auto-generated (do not commit)
  shard_graph.json            ← auto-generated (do not commit)
  nova_usage.jsonl            ← auto-generated (do not commit)
  nova_autoresearch.jsonl     ← autoresearch output log (do not commit)
  .env                        ← local config (do not commit)
  .env.example                ← config template (committed)
  KNOWN_ISSUES.md             ← open and resolved bug log
  .gitignore
  README.md
  ROADMAP.md
```

---

## NOVA MCP Tools (v2 — 12 tools)

| Tool | Description |
|---|---|
| `nova_shard_interact` | Load shards into context. Auto-selects by confidence-weighted relevance. Start every session with this. |
| `nova_shard_create` | Create a new shard with a guiding question. Triggers post-write enrichment and graph registration. |
| `nova_shard_update` | Append a conversation turn to an existing shard. Triggers enrichment and auto-compaction at 30 turns. |
| `nova_shard_search` | Search shard content with confidence weighting. High-confidence shards rank higher. |
| `nova_shard_list` | List all shards sorted by confidence score with metadata. |
| `nova_shard_get` | Read the full raw content of a shard with no side effects. |
| `nova_shard_merge` | Merge two or more shards into a new meta-shard. Auto-wires graph relations. |
| `nova_shard_archive` | Soft-archive a shard. Excluded from search but content is preserved. |
| `nova_shard_forget` | Permanently exclude a shard from all retrieval. Records provenance log entry. Content is not physically deleted. |
| `nova_shard_consolidate` | Run full maintenance cycle: confidence decay + compaction + merge suggestions. |
| `nova_graph_query` | Query the inter-shard knowledge graph by source, target, or relation type. |
| `nova_graph_relate` | Manually add a directed relation between two shards. |

### Shard Schema

```json
{
  "shard_id": "string",
  "guiding_question": "string",
  "conversation_history": [...],
  "meta_tags": {
    "intent": "reflection | planning | research | brainstorm | archive | forgotten | meta_synthesis",
    "theme": "string",
    "usage_count": 0,
    "confidence": 1.0,
    "last_accessed": "ISO8601"
  },
  "embedding": [...]
}
```

---

## Forgemaster Skill Library

Load the relevant skill before each operation. All skills live in `forgemaster/skills/`.

| Skill | When to use |
|---|---|
| `forgemaster-orchestrator` | Starting a sprint, routing tickets |
| `forgemaster-parallel-lanes` | Dispatching 2+ independent tickets |
| `forgemaster-writing-plans` | Decomposing a design doc into tickets |
| `forgemaster-implementation` | Executing a single ticket |
| `forgemaster-systematic-debugging` | Investigating any bug or failure |
| `forgemaster-verification` | Before claiming any work is complete |
| `forgemaster-git-workflow` | Branch setup, integration, PR creation |
| `forgemaster-code-review` | Two-stage review after implementation |
| `forgemaster-qa-review` | Structural QA review of LLM-generated code |
| `forgemaster-nova-session-handoff` | Persisting state across session boundaries |

---

## Utilities

### Migrate ChatGPT history to NOVA shards

```bash
# Dry run — preview without writing
python tools/chatgpt_to_nova.py --dry-run

# Run migration
python tools/chatgpt_to_nova.py
```

### Autoresearch loop (requires LM Studio)

Runs Qwen locally via LM Studio and writes research findings directly as NOVA shards.

```bash
python tools/autoresearch.py              # run all topics
python tools/autoresearch.py --topic qa   # run topics tagged 'qa'
python tools/autoresearch.py --dry-run    # preview without running
```

Requires LM Studio running at `http://127.0.0.1:1234` with a model loaded.

### Rebuild shard index

```bash
cd python && python shard_index.py
```

### Batch enrich shards with embeddings

```bash
cd python && python context_extractor.py
```

---

## Notes

- Never manually edit files in `shards/` — always use the MCP tools
- `shard_index.json`, `shard_graph.json`, `nova_usage.jsonl`, and `nova_autoresearch.jsonl` are auto-generated and must not be committed
- No OpenAI API key is required — embeddings are generated locally via `all-MiniLM-L6-v2`
- Shards with confidence < 0.4 are tagged `low_confidence` and excluded from default search; pass `include_low_confidence=True` to recall them
- Run `nova_shard_consolidate` every 3 sprints to decay stale shards, compact bloated ones, and surface merge candidates
- The `_deprecated/` folder in `mcp/` holds v1 reference files — do not use them for new work
