# NOVA + Forgemaster — Code and Project Health Check

## What You Are Doing

You are running a full health check on the NOVA v2 + Forgemaster codebase. Check both the project structure and the code quality. Report what works, what has bugs, and what needs fixing before the system can be used in production.

---

## Step 1: Verify the Import Chain

Check `mcp/nova_server.py`:

1. Find the import line for `nova_embeddings_local` — should be near the top
2. Search the entire file for any function definitions named `enrich_shard` or `_generate_compaction_summary` AFTER the import line
3. If either function is defined locally after the import, that is a **critical bug** — the import is shadowed and the local version runs instead of the imported one
4. Report exactly which lines the import is on and whether any shadowing definitions exist

---

## Step 2: Verify nova_embeddings_local.py

Check `mcp/nova_embeddings_local.py`:

1. Confirm `get_embedding_model()` loads `all-MiniLM-L6-v2` from sentence-transformers
2. Confirm `enrich_shard` does NOT import or call `openai` anywhere (note: function is `enrich_shard`, not `enrich_shard_async`)
3. Confirm `_generate_compaction_summary` does NOT import or call `openai` anywhere
4. Check the `generate_local_embedding` function returns a list of floats or None
5. Check error handling — confirm failures don't crash the server, just set enrichment_status

---

## Step 3: Verify Shard Schema

Pick 3 shards from `shards/` — one ai_ml, one game_design, one personal theme. For each:

1. Confirm required fields exist: `shard_id`, `guiding_question`, `conversation_history`, `meta_tags`
2. Check `meta_tags` has: `intent`, `theme`, `usage_count`, `last_used`, `confidence`
3. Check if `context.embedding` exists — if yes, shards are enriched; if no, they need the batch enrichment script
4. Check `conversation_history` format — each entry should have `timestamp`, `user`, `ai`
5. Confirm no shard filenames start with `chatgpt_` — the prefix was stripped in a bulk rename

---

## Step 4: Verify the 18 MCP Tools

In `nova_server.py`, confirm all 18 NOVA tools are defined with `@mcp.tool`:

1. nova_shard_interact
2. nova_shard_create
3. nova_shard_update
4. nova_shard_search
5. nova_shard_index
6. nova_shard_summary
7. nova_shard_list
8. nova_shard_get
9. nova_shard_merge
10. nova_shard_archive
11. nova_shard_forget
12. nova_shard_consolidate
13. nova_graph_query
14. nova_graph_relate
15. nova_session_flush
16. nova_session_load
17. nova_session_list
18. nova_forgemaster_sprint

For each tool, check the function signature matches its input model in `schemas.py`.

Additionally, verify `mcp/Gemini/gemini_mcp.py` defines 2 tools:
- gemini_execute_ticket
- gemini_load_file

---

## Step 5: Verify the Knowledge Graph Functions

In `mcp/graph.py` (not nova_server.py):

1. Find `load_graph()`, `save_graph()`, `add_relation()`, `query_graph()`
2. Confirm `GRAPH_FILE` path resolves to repo root (not inside `mcp/`)
3. In `nova_server.py`, confirm `add_shard_to_graph` is called in `nova_shard_create`
4. In `nova_server.py`, confirm `add_relation` is called in `nova_shard_merge` with `extends` relation type

---

## Step 6: Verify the Index and Path Resolution

In `mcp/config.py` (constants are centralised here — not inline in nova_server.py):

1. Confirm `SHARD_DIR`, `INDEX_FILE`, `GRAPH_FILE`, `USAGE_LOG_FILE` are defined
2. Confirm they use `_REPO_ROOT = Path(__file__).parent.parent` as base
3. This means when `nova_server.py` runs from `mcp/`, all files resolve to repo root
4. Confirm `nova_server.py` imports these constants from `config` rather than re-defining them
5. Confirm `os.makedirs(SHARD_DIR, exist_ok=True)` is called at startup in `nova_server.py`

---

## Step 7: Verify Confidence Decay Logic

In `mcp/maintenance.py` (not nova_server.py):

1. Find `apply_confidence_decay`
2. Confirm the formula: `max(0.1, confidence * (1.0 - DECAY_RATE))`
3. Confirm it only decays when `days_since >= DECAY_INTERVAL_DAYS`
4. Confirm `DECAY_RATE` defaults to `0.05` and `DECAY_INTERVAL_DAYS` defaults to `7` (sourced from `config.py`)
5. In `nova_server.py`, check that confidence boost on access exists in `nova_shard_interact` — should be `min(1.0, confidence + 0.05)`

---

## Step 8: Check the Forgemaster Skill Files

For each file in `forgemaster/skills/`:

1. Confirm it has a YAML frontmatter block with `name` and `description`
2. Confirm it has at least one `## When to Use` section
3. Check for any references to OpenAI, GPT, or external API calls — there should be none
4. Confirm `forgemaster-nova-session-handoff.md` references `nova_shard_update` and `nova_shard_interact` by name

Expected files (10 total):
- forgemaster-orchestrator.md
- forgemaster-parallel-lanes.md
- forgemaster-writing-plans.md
- forgemaster-implementation.md
- forgemaster-systematic-debugging.md
- forgemaster-verification.md
- forgemaster-git-workflow.md
- forgemaster-code-review.md
- forgemaster-qa-review.md
- forgemaster-nova-session-handoff.md

---

## Step 9: Check CLAUDE.md

1. Confirm `CLAUDE.md` exists at repo root
2. Confirm it references `nova_server.py` as the active server
3. Confirm it has the session handoff protocol section
4. Confirm it has the sprint workflow section
5. Confirm it does NOT reference OpenAI key as required
6. Confirm it references `utilities/` for shard_index.py and chatgpt_to_nova.py (not `python/` or `tools/`)
7. Confirm it lists 3 env vars added in the last refactor: `CONFIDENCE_THRESHOLD`, `GEMINI_MODEL`, `GEMINI_API_KEY`

---

## Step 10: Identify Any Bugs or Issues

After completing all checks, list:

**Critical bugs** — things that will cause the server to fail or produce wrong results
**Warnings** — things that work but are not ideal
**Missing pieces** — things referenced in the code but not yet implemented

---

## Output Format

Produce a structured report:

```
HEALTH CHECK REPORT — NOVA v2 + Forgemaster
Date: [today]

CRITICAL ISSUES:
[list or "None found"]

WARNINGS:
[list or "None found"]

MISSING PIECES:
[list or "None found"]

TOOL INVENTORY:
[list of 18 NOVA tools + 2 Gemini tools with ✅ confirmed / ❌ missing]

SHARD SAMPLE:
[findings from 3 sampled shards]

ENRICHMENT STATUS:
[whether embeddings are present or batch enrichment needed]

OVERALL STATUS:
[Ready to run / Needs fixes before running]

RECOMMENDED NEXT ACTIONS:
[ordered list]
```

---

## Important Rules

- Read actual file contents — do not assume
- Do not modify any files during this check
- Do not run any Python code
- If you find the shadowing bug (local function definitions after import), flag it as CRITICAL immediately
- Be honest about what is missing — the goal is to find problems, not to give a clean report
