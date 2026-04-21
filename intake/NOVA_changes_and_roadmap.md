# NOVA: Changes Shipped and Roadmap

Synthesized from conversations March 2026 to April 2026. Focused on architectural changes made and planned additions, not general discussion.

---

## Current State (as of April 2026)

NOVA is open-sourced at `github.com/Vintersong/NOVA-Cognition-Framework` under Apache 2.0 (relicensed from MIT on April 3 after the OpenClaw attribution incident). Both NOVA and Forgemaster went public April 4, timed to the Anthropic third-party harness cutoff.

**Operational numbers:**
- 18 MCP tools (up from 7 in v1, then 11, then 16)
- 425+ shards
- Forgemaster: 320 agent definitions across 19 divisions, 208 skill library files across 13 categories

**Modules confirmed working:** store, graph, maintenance, ravens, nott, hooks.

---

## Changes Shipped (v1 to v2)

Built during the March to early April sprint. Everything below is in the repo, not roadmap.

**Two-speed retrieval (HUGINN / MUNINN).** Fast pass followed by deep rerank. Biological parallel to recognition-before-recall. Tradeoff explicitly chosen between retrieval speed and accuracy.

**NÓTT maintenance daemon.** Background process for shard upkeep. Handles decay, merge suggestions, consolidation cycles.

**Confidence decay.** Float field per shard, OpenFang-style formula. Solves shard bloat by letting old/unused knowledge degrade rather than remain equally trusted.

**Auto-compaction.** Summarize at 30-turn threshold, keep last 15. Prevents conversation history from ballooning individual shards.

**Post-write enrichment hooks.** Auto-trigger `context_extractor` on shard create/update. No more manual enrichment passes.

**Similarity-based merge suggestions.** Cosine > 0.85 flags shards for merge review.

**Knowledge graph layer.** JSON adjacency MVP, SQLite planned for production. Adds three tools: `forget`, `consolidate`, `graph_query`.

**Usage tracking per agent lane.** `nova_usage.jsonl` logs access patterns. Becomes the substrate for emotional state routing later.

**Local embeddings.** `sentence-transformers` (all-MiniLM-L6-v2) replaces OpenAI dependency. Difflib fallback when no embeddings available. Three-tier graceful degradation: offline works, local models better, API best.

**Gemini Flash integration.** Wired as a registered MCP tool. Part of Forgemaster's routing table.

**Three-tier tool hierarchy (shipped).** Replaces `nova_shard_list` at scale:
- `nova_shard_index` - compact rows, no conversation history loaded (~100-300 chars/shard)
- `nova_shard_summary` - index row + Haiku-generated 1-2 sentence synopsis (~200-500 chars/shard)
- `nova_shard_get` - full content, one shard at a time (unchanged)

**`nova_shard_quick.md` skill.** Universal shard authoring skill. Any MCP-compatible model can produce valid NOVA shard JSON without manual formatting. Ephemeral-to-permanent promotion: shards start ephemeral, graduate to permanent based on usage frequency.

**Haiku-powered shard index compression pipeline.** Batch script scans shards missing `summary_sentence`, batches 5 per Haiku call, writes to `summary_index.json` and `summary_index.md`. Resumable. Solves the 150k-character `nova_shard_list` problem and the queued ChatGPT migration retitling as a side effect.

**ChatGPT export migration.** 425 shards imported across themes (ai_ml, game_design, creative, general, research, personal, technical, philosophy, career).

**`autoresearch.py`.** Runs Nemotron overnight against research topics, writes findings as shards.

**Gemini worker MCP server.** Debugged and operational. `.env` consolidated so `gemini_worker.py` and Stitch MCP share a single Google API key.

---

## Current Shard Schema

```json
{
  "shard_id": "string",
  "guiding_question": "string",
  "conversation_history": [...],
  "meta_tags": {
    "intent": "reflection | planning | research | brainstorm...",
    "theme": "string",
    "usage_count": 0,
    "last_used": "ISO 8601",
    "confidence": 1.0,
    "enrichment_status": "enriched | pending | failed"
  },
  "context": {
    "summary": "auto-generated",
    "topics": ["tag1", "tag2"],
    "embedding": [...],
    "last_context_update": "ISO 8601"
  }
}
```

---

## Roadmap (Planned, Not Yet Implemented)

From the README roadmap section drafted April 9, plus later additions.

**Emotional State Routing.** Anthropic's functional-emotions research showed a desperation vector spikes when models face impossible constraints, producing reward hacking (gaming test suites, deleting tests). NOVA already tracks confidence per shard. Plan: add an emotional state vector alongside confidence so Forgemaster routes on agent state, not just task complexity. High urgency + low confidence = skip HUGINN, escalate to MUNINN or above. Desperation intercepted before it produces reward hacking. `nova_usage.jsonl` already logs the patterns needed to detect the trajectory. Shard written (`emotional_state_routing`). Forgemaster skill not yet built.

**Local Thematic Analysis Pipeline.** Automated shard tagging using local models (Nemotron, Devstral, Ministral) running in LM Studio. Zero API cost. Feeds back into NOVA as enrichment metadata. Closes the loop between retrieval quality and shard organization. Built and partially run.

**SCT Pocket Model.** Fine-tune a 7 to 14B parameter model on exported NOVA shards using Spectral Compact Training. Embeds the author's reasoning patterns at the weight level. `nova_sct_export.py`, `nova_sct_finetune.py`, and `COPILOT_PROMPT_nova_sct.md` are written. Compute and a clean training run are the remaining blockers.

**New Shard Serialization Layer.** Context-size problem: shards blow context windows. Proposed stack:
- YAML stub (DNS layer, metadata only, no content)
- Markdown summary (human-readable middle layer)
- Full JSON shard (on-demand)

YAML frontmatter + Markdown body format agreed April 2026. Old JSON shards remain readable. Conversion happens lazily on merge or consolidation (NÓTT cycle).

Example target format:
```yaml
---
id: nova-shard-001
type: skill | tool | agent | memory | design | conversation
confidence: 0.87
created: 2026-04-10
last_used: 2026-04-10
tags: [python, agent, retrieval]
source_agent: claude | copilot | gemini | local
helminth:
  - source: vscode-nova-extension
    date: 2026-04-10
    agent: claude-sonnet-4-6
    similarity: 0.91
full_ref: shards/nova-shard-001-full.shard
---
# Brief compacted summary
```

**Multi-user / DNS metadata extension.** Minimal schema additions: `origin_id`, `tier` (personal/department/studio), `blast_radius_score`, `subscription_topics`. Overnight Opus pass populates blast radius and updates subscription topics based on the day's new shards. Enables morning briefing per department.

**Merge-depth tracking.** Compression trades fidelity for fit; knowledge graphs drift on merge cycles. Plan: same mechanism as confidence decay, tracking how many summarization passes a shard has survived. High merge depth = low retrieval trust, flag for full shard pull. Summaries for routing, originals for grounding.

**VS Code extension (`nova-vscode`).** Native integration. WebView dashboard, shard browser, graph view, context panel. Auto-ingestion from Copilot / Continue / Claude Code conversations. Three-tier retrieval injecting context before next prompt. Architecture documented; not built.

**LongMemEval benchmarking adapter.** Scoped. Uses HuggingFace dataset. Haiku-powered adapter script as implementation path. Tests NOVA against the same benchmarks MemPalace is running.

---

## NOVA v3 (Conceptual, Older Notes)

Carried from December 2025 / early discussions. Not actively scheduled.

- Multi-model consensus writes (Le Chat as arbiter/gatekeeper, Claude as middleman/orchestrator, ChatGPT as contributor)
- Cross-model memory sharing with provenance tracking
- Embedding activation logic embedded inside shards rather than external orchestration
- Full automation dial, human-directed to fully autonomous depending on use case

---

## Outstanding Loose Ends

Flagged across recent sessions, not yet fixed:

- README has duplicate sections from v1/v2 merge
- GitHub description said "stateless recursive" when the system is fully stateful (fixed to "Persistent shard-based memory and multi-agent orchestration for AI agents, served over MCP")
- Pitch docs sitting in repo root alongside source
- White paper update pending
- LongMemEval test not yet run
- SCT training run blocked on compute
- Emotional state routing Forgemaster skill not yet written
