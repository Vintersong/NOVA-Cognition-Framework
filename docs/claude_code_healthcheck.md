# NOVA + Forgemaster — Code and Project Health Check

## What You Are Doing

You are running a full health check on the NOVA v2 + Forgemaster codebase. Check both the project structure and the code quality. Report what works, what has bugs, and what needs fixing before the system can be used in production.

---

## Step 1: Verify the Import Chain

Check `mcp/nova_server_v2.py`:

1. Find the import line for `nova_embeddings_local` — should be near the top
2. Search the entire file for any function definitions named `enrich_shard_async` or `_generate_compaction_summary` AFTER the import line
3. If either function is defined locally after the import, that is a **critical bug** — the import is shadowed and the local OpenAI version runs instead of the local embedding version
4. Report exactly which lines the import is on and whether any shadowing definitions exist

---

## Step 2: Verify nova_embeddings_local.py

Check `mcp/nova_embeddings_local.py`:

1. Confirm `get_embedding_model()` loads `all-MiniLM-L6-v2` from sentence-transformers
2. Confirm `enrich_shard_async` does NOT import or call `openai` anywhere
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

---

## Step 4: Verify the 11 MCP Tools

In `nova_server_v2.py`, confirm all 11 tools are defined with `@mcp.tool`:

1. nova_shard_interact
2. nova_shard_create
3. nova_shard_update
4. nova_shard_search
5. nova_shard_list
6. nova_shard_merge
7. nova_shard_archive
8. nova_shard_forget
9. nova_shard_consolidate
10. nova_graph_query
11. nova_graph_relate

For each tool, check the function signature matches its input model.

---

## Step 5: Verify the Knowledge Graph Functions

In `nova_server_v2.py`:

1. Find `load_graph()`, `save_graph()`, `add_shard_to_graph()`, `add_relation()`, `query_graph()`
2. Confirm `GRAPH_FILE` path resolves to repo root (not inside `mcp/`)
3. Confirm `add_shard_to_graph` is called in `nova_shard_create`
4. Confirm `add_relation` is called in `nova_shard_merge` with `extends` relation type

---

## Step 6: Verify the Index and Path Resolution

1. Find `SHARD_DIR`, `INDEX_FILE`, `GRAPH_FILE`, `USAGE_LOG_FILE` definitions
2. Confirm they use `_REPO_ROOT = Path(__file__).parent.parent` as base
3. This means when `nova_server_v2.py` runs from `mcp/`, all files resolve to repo root
4. Confirm `os.makedirs(SHARD_DIR, exist_ok=True)` is called at startup

---

## Step 7: Verify Confidence Decay Logic

In `apply_confidence_decay`:

1. Confirm the formula: `max(0.1, confidence * (1.0 - DECAY_RATE))`
2. Confirm it only decays when `days_since >= DECAY_INTERVAL_DAYS`
3. Confirm `DECAY_RATE` defaults to `0.05` and `DECAY_INTERVAL_DAYS` defaults to `7`
4. Check that confidence boost on access exists in `nova_shard_interact` — should be `min(1.0, confidence + 0.05)`

---

## Step 8: Check the Forgemaster Skill Files

For each file in `forgemaster/skills/`:

1. Confirm it has a YAML frontmatter block with `name` and `description`
2. Confirm it has at least one `## When to Use` section
3. Check for any references to OpenAI, GPT, or external API calls — there should be none
4. Confirm `forgemaster-nova-session-handoff.md` references `nova_shard_update` and `nova_shard_interact` by name

---

## Step 9: Check CLAUDE.md

1. Confirm `CLAUDE.md` exists at repo root
2. Confirm it references `nova_server_v2.py` as the active server
3. Confirm it has the session handoff protocol section
4. Confirm it has the sprint workflow section
5. Confirm it does NOT reference OpenAI key as required

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
[list of 11 tools with ✅ confirmed / ❌ missing]

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
