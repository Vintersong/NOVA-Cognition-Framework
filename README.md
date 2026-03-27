# NOVA-Cognition-Framework

A unified repository containing **NOVA** (persistent AI memory) and **Forgemaster** (multi-agent orchestration). NOVA is the memory layer. Forgemaster is the execution layer. They share one repo and one data store.

---

## What is NOVA?

NOVA is a persistent memory system for AI agents. It stores conversations and decisions as modular JSON "shards" — each with metadata, confidence scores, and decay rates. A knowledge graph tracks relationships between shards so agents can retrieve contextually relevant memory, not just recent history.

NOVA runs as an MCP server, meaning any MCP-compatible client (Claude Desktop, Claude Code, Cursor) can connect to it and query/write memory using structured tool calls.

**Key capabilities:**
- Store and retrieve memory shards with semantic search
- Confidence-weighted recall with time-based decay
- Automatic deduplication and shard consolidation
- Knowledge graph of shard relationships
- Usage logging for memory health monitoring

---

## What is Forgemaster?

Forgemaster is a multi-agent orchestration layer built on top of NOVA. It decomposes tasks into typed tickets and routes each to the optimal model — Claude for architecture and review, Gemini Flash for implementation and boilerplate, GPT-4o for research and documentation.

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
  │       └── gpt-4o         → research, documentation
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
# Edit .env and set your OPENAI_API_KEY
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
      "args": ["path/to/mcp/nova_server_v2.py"]
    }
  }
}
```

---

## Directory Structure

```
NOVA-Cognition-Framework/
  mcp/
    nova_server_v2.py       ← active MCP server (11 tools)
    SKILL_v2.md             ← active cognitive instructions
    requirements.txt
    nova_server.py          ← v1 reference (do not delete)
    SKILL.md                ← v1 reference (do not delete)
  python/
    shard_index.py          ← index management (used by v2)
    context_extractor.py    ← batch semantic enrichment
    dedup_json.py
    rename_shards.py
    main.py
  shards/                   ← live shard data (do not modify manually)
  forgemaster/
    AGENTS.md               ← global agent config and routing rules
    skills/
      forgemaster-orchestrator.md
      forgemaster-parallel-lanes.md
      forgemaster-writing-plans.md
      forgemaster-implementation.md
      forgemaster-systematic-debugging.md
      forgemaster-verification.md
      forgemaster-git-workflow.md
      forgemaster-code-review.md
      forgemaster-nova-session-handoff.md
  tools/
    chatgpt_to_nova.py      ← ChatGPT export migration script
  shard_index.json          ← auto-generated (do not commit)
  shard_graph.json          ← auto-generated (do not commit)
  nova_usage.jsonl          ← auto-generated (do not commit)
  .env                      ← your secrets (do not commit)
  .env.example              ← template (committed)
  .gitignore
  README.md
```

---

## NOVA MCP Tools (v2)

| Tool | Description |
|---|---|
| `nova_shard_interact` | Query shards by semantic similarity |
| `nova_shard_update` | Write or update a shard |
| `nova_shard_consolidate` | Merge and prune low-confidence shards |
| `nova_shard_list` | List all shards with metadata |
| `nova_shard_delete` | Remove a shard by ID |
| `nova_graph_query` | Traverse the knowledge graph |
| `nova_graph_link` | Create a relationship between shards |
| `nova_index_rebuild` | Rebuild the full shard index |
| `nova_usage_log` | View recent tool usage |
| `nova_health_check` | Memory health summary |
| `nova_shard_search` | Full-text search across shard content |

---

## Notes

- Never manually edit files in `shards/` — always go through the MCP tools
- `shard_index.json`, `shard_graph.json`, and `nova_usage.jsonl` are auto-generated and should not be committed
- `.env` contains your OpenAI key — never commit it
- `nova_server.py` and `SKILL.md` are kept as v1 reference — do not delete
