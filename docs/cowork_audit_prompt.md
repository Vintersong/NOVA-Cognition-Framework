# NOVA + Forgemaster — Progress Audit

## What You Are Doing

You are auditing the current state of the NOVA + Forgemaster project and producing a clear progress report. Check what exists, what is missing, and what needs to happen next. Be specific and honest — do not assume something works just because the file exists.

## Repo Location

```
C:\Users\Moldo\Master Project NOVA\repos\forgemaster-harvest\NOVA-Cognition-Framework\
```

---

## What Should Exist — Check Each Item

### NOVA v2 Core (mcp/)

- [ ] `mcp/nova_server.py` — the active server with 16 tools
- [ ] `mcp/nova_embeddings_local.py` — local sentence-transformers embedding functions
- [ ] `mcp/SKILL_v2.md` — v2 cognitive architecture instructions
- [ ] `mcp/requirements.txt` — should contain `sentence-transformers`, NOT `openai`
- [ ] `mcp/nova_server.py` — v1 reference (should exist but not be active)
- [ ] `mcp/SKILL.md` — v1 reference (should exist)

**Check nova_server.py for:**
- Line ~60: `from nova_embeddings_local import enrich_shard, _generate_compaction_summary`
- No local definition of `enrich_shard` below that import (would shadow it)
- No local definition of `_generate_compaction_summary` below that import
- No active `OPENAI_API_KEY` usage (should be commented out or removed)

**Check requirements.txt for:**
- `sentence-transformers>=2.2.0` present
- `openai` absent

### Shards

- [ ] `shards/` directory exists at repo root
- [ ] Contains 400+ JSON shard files (migrated from ChatGPT export)
- [ ] Check one shard file — confirm it has `shard_id`, `guiding_question`, `conversation_history`, `meta_tags`
- [ ] Check `meta_tags.enrichment_status` on a few shards — if all are `pending` or `pending_no_key`, embeddings haven't been generated yet and merge suggestions won't work

### Forgemaster Skills

- [ ] `forgemaster/AGENTS.md` exists
- [ ] `forgemaster/skills/` directory exists with exactly 9 files:
  - forgemaster-orchestrator.md
  - forgemaster-parallel-lanes.md
  - forgemaster-writing-plans.md
  - forgemaster-implementation.md
  - forgemaster-systematic-debugging.md
  - forgemaster-verification.md
  - forgemaster-git-workflow.md
  - forgemaster-code-review.md
  - forgemaster-nova-session-handoff.md

### Configuration Files

- [ ] `CLAUDE.md` at repo root — Claude Code instructions
- [ ] `.env.example` at repo root — environment variable template
- [ ] `.gitignore` at repo root — should exclude `.env`, `shards/`, `shard_index.json`, `shard_graph.json`, `nova_usage.jsonl`
- [ ] `.mcp.json` or equivalent — MCP server configuration for Claude Desktop/Code

### Tools

- [ ] `tools/chatgpt_to_nova.py` — migration script

### Auto-generated Files (should exist after first run)

- [ ] `shard_index.json` at repo root — auto-generated index
- Check if it exists and has content

### Missing / Not Yet Done

- [ ] `shard_graph.json` — knowledge graph, created on first run of v2 server
- [ ] Shards enriched with local embeddings — need to run batch enrichment script
- [ ] `.env` file created from `.env.example` (not committed, but should exist locally)
- [ ] Claude Desktop config updated to point at `nova_server.py`

---

## What To Report

Produce a clear status report with three sections:

**DONE — verified present and correct:**
List each item confirmed working with brief note on what you checked.

**PRESENT BUT NEEDS ATTENTION:**
List items that exist but have issues — wrong content, shadowed imports, missing dependencies, etc.

**MISSING — needs to be created or fixed:**
List each missing item with exactly what needs to happen.

**NEXT ACTIONS — ordered by priority:**
List the 3-5 most important things to do next to get NOVA v2 running, in order.

---

## Do Not Do

- Do not modify any files during this audit
- Do not run any code
- Do not assume a file works just because it exists — read the relevant sections
- Do not check the `mcp/shards/` subdirectory — the active shards are at repo root `shards/`
