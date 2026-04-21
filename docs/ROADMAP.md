# NOVA Cognition Framework — Roadmap

Last synced: April 2026. Source of truth for current state is `CLAUDE.md`; source for the shipped-vs-planned split is `intake/NOVA_changes_and_roadmap.md`.

---

## Shipped

Fully implemented and in use.

**NOVA MCP server (30 tools)**
- 18 core shard + graph + session tools registered in `mcp/nova_server.py`
- 6 wiki tools (`mcp/wiki_tools.py`)
- 3 Nidhogg repo-scanner tools (`mcp/nidhogg.py`)
- 1 evolution tool (`mcp/evolve.py`)
- 2 Gemini tools registered into nova_server via `mcp/Gemini/gemini_mcp.py`

**Retrieval and maintenance**
- Two-speed retrieval: HUGINN (Haiku fast pass) + MUNINN (Sonnet deep rerank), `mcp/ravens.py`
- NÓTT background daemon for decay, compaction, merge suggestions, graph sync (`mcp/nott.py`)
- Confidence decay with OpenFang-style formula
- Auto-compaction at 30-turn threshold, keeps last 15
- Similarity-based merge suggestions (cosine > 0.85)
- Post-write enrichment hooks via `mcp/hooks.py`
- Local embeddings through `all-MiniLM-L6-v2` with difflib fallback — no external API required
- Three-tier shard discovery: `nova_shard_index` → `nova_shard_summary` → `nova_shard_get`
- Haiku-powered `summary_index.json` compression pipeline (`mcp/build_summary_index.py`)
- Knowledge graph layer with five relation types and transitive BFS
- Usage tracking per operation to `nova_usage.jsonl`
- File-locking for concurrent write safety

**Forgemaster orchestration**
- 10 core orchestration skills in `forgemaster/skills/`
- 208 domain skills across 15 categories in `forgemaster/library/`
- 326 agent persona definitions across 18 divisions in `forgemaster/agents/`
- `AGENTS.md` routing config, `STANDARDS.md` authoring standard, `SKILL_LIBRARY.md` index

**Utilities**
- `utilities/chatgpt_to_nova.py` — ChatGPT export migration (425 shards imported)
- `utilities/autoresearch.py` — local LLM research loop writing findings as shards
- `utilities/shard_index.py` — manual index rebuild
- `utilities/dedup_json.py` — duplicate shard detection
- `utilities/shard_compact.py` — manual compaction helper
- `utilities/theme_analyzer.py` — theme distribution analysis

**Operational metrics (April 2026):** 425+ shards, 18 core MCP tools, modules confirmed working: store, graph, maintenance, ravens, nott, hooks.

---

## Planned

Scoped but not yet implemented.

**Procedural memory tier.** New `shard_type: "procedural"` capturing recurring patterns as named workflows (trigger condition + ordered steps). Extracted by `nova_shard_consolidate` from shards tagged `pattern` or accessed ≥ 3 times; requires ≥ 2 recurring patterns before extraction fires. Reinforced on successful reuse (`strength += 0.1`, cap 1.0). Typed `references` field lets procedural shards cite shards, wiki pages, and skill files together. Forgemaster surfaces matching procedures at sprint start as suggested skill paths. NÓTT reports gap/evolution candidates as proposals only — nothing auto-applies. Borrowed from agentmemory consolidation pipeline.

**Obsidian auto-export.** `nova_obsidian_export` tool emits one `.md` per shard with YAML frontmatter and `[[wikilink]]` edges derived from the knowledge graph, so the shard layer renders natively in Obsidian's graph view. Configurable `NOVA_OBSIDIAN_DIR` (default `output/obsidian_vault/`), optional `NOVA_OBSIDIAN_AUTO_EXPORT` hook after `nova_shard_consolidate`. No new dependencies — pure file I/O over existing shard and graph data. Wiki pages are already Obsidian-compatible; this covers the shard layer.

**Emotional state routing.** Add an emotional state vector alongside per-shard confidence so Forgemaster routes on agent state, not just task complexity. High urgency + low confidence escalates past HUGINN to MUNINN or above, intercepting desperation-driven reward hacking before it happens. `nova_usage.jsonl` already logs the trajectory data. Shard written (`emotional_state_routing`); Forgemaster skill not yet built.

**Local thematic analysis pipeline.** Automated shard tagging using local models (Nemotron, Devstral, Ministral) in LM Studio. Zero API cost. Feeds enrichment metadata back into NOVA. Built and partially run.

**SCT Pocket Model.** Fine-tune a 7–14B parameter model on exported NOVA shards using Spectral Compact Training. Scripts written (`nova_sct_export.py`, `nova_sct_finetune.py`, `COPILOT_PROMPT_nova_sct.md`). Blocked on compute.

**New shard serialization layer.** YAML frontmatter + Markdown body + on-demand full JSON. Solves shards blowing context windows. Conversion happens lazily on merge or consolidation (NÓTT cycle). Old JSON shards remain readable. Format agreed April 2026; not yet converting.

**Multi-user / DNS metadata extension.** Adds `origin_id`, `tier` (personal/department/studio), `blast_radius_score`, `subscription_topics`. Overnight Opus pass populates blast radius. Enables per-department morning briefing.

**Merge-depth tracking.** Same mechanism as confidence decay but for summarization passes. High merge depth → low retrieval trust → flag for full-shard pull. Summaries for routing, originals for grounding.

**VS Code extension (`nova-vscode`).** WebView dashboard, shard browser, graph view, context panel. Auto-ingestion from Copilot / Continue / Claude Code conversations. Three-tier retrieval injecting context before next prompt. Architecture documented; not built.

**LongMemEval benchmarking adapter.** Scoped. HuggingFace dataset, Haiku-powered adapter script. Tests NOVA against the same benchmarks MemPalace runs.

**Claude Code hooks integration.** Move session handoff and context loading from voluntary protocol to enforced hooks. Four patterns borrowed from awrshift/claude-memory-kit:
- `SessionStart` hook prints `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "..."}}` to stdout — auto-injects `nova_shard_interact` output every session so the model can't forget to call it.
- `PreCompact` hook returns `{"decision": "block", "reason": "..."}` until the handoff shard is written (mtime check on target shard file) — enforces the "write before ending" rule currently in CLAUDE.md.
- `CLAUDE_INVOKED_BY` env-var recursion guard for any hook that spawns `claude -p` subprocesses (prevents infinite SessionEnd → flush → claude -p → SessionEnd loops).
- Silent-failure guard: snapshot target-file mtimes before calling a sub-Claude pipeline, verify at least one file changed afterwards, re-queue the job on no-op. Prevents sub-agents that describe-instead-of-do from silently corrupting state.
Upstream reference: github.com/awrshift/claude-memory-kit.

---

## Conceptual (NOVA v3, not scheduled)

Carried from December 2025 / early discussions.

- Multi-model consensus writes (Le Chat arbiter, Claude orchestrator, ChatGPT contributor)
- Cross-model memory sharing with provenance tracking
- Embedding activation logic embedded inside shards rather than in external orchestration
- Full automation dial, human-directed to fully autonomous per use case

---

## Outstanding Loose Ends

Flagged recently, not yet fixed:

- README has duplicate sections from v1/v2 merge
- White paper update pending
- LongMemEval test not yet run
- SCT training run blocked on compute
- Emotional state routing Forgemaster skill not yet written
- `nova_shard_create` cold-start timeout — `enrich_shard` hook stalls on first run before embedding weights cache. Tracked in `KNOWN_ISSUES.md`
- Stitch MCP integration blocked by auth timeout. Tracked in `KNOWN_ISSUES.md #1`
