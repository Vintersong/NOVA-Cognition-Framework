# NOVA Cognition Framework — Roadmap

---

## Current

Fully implemented and functional.

**NOVA MCP Server (v2)**
- 12 MCP tools: `nova_shard_interact`, `nova_shard_create`, `nova_shard_update`, `nova_shard_search`, `nova_shard_list`, `nova_shard_get`, `nova_shard_merge`, `nova_shard_archive`, `nova_shard_forget`, `nova_shard_consolidate`, `nova_graph_query`, `nova_graph_relate`
- Confidence scoring with time-based decay (configurable rate and interval)
- Auto-compaction at configurable turn threshold (default: 30)
- Post-write enrichment hooks — local embeddings via `all-MiniLM-L6-v2` on every create/update
- Similarity merge suggestions — cosine threshold flagging (default: 0.85)
- Inter-shard knowledge graph with five relation types: `influences`, `depends_on`, `contradicts`, `extends`, `references`
- Transitive graph traversal (`nova_graph_query`)
- Fuzzy + cosine retrieval with `difflib` fallback
- File-locking for concurrent write safety
- Usage logging per operation to `nova_usage.jsonl`
- MCP resource endpoints: `nova://skill`, `nova://index`, `nova://graph`, `nova://usage`
- No external API dependency — fully local

**Forgemaster Skill Library**
- 10 skills covering orchestration, implementation, debugging, verification, code review, QA review, git workflow, and session handoff
- `AGENTS.md` routing config with model-to-task-type mapping
- `STANDARDS.md` and `SKILL_LIBRARY.md` reference docs

**Utilities**
- `tools/chatgpt_to_nova.py` — migrate ChatGPT export JSON to NOVA shards
- `tools/autoresearch.py` — local LLM research loop (Qwen via LM Studio) that writes findings as NOVA shards; supports `--topic` and `--dry-run` flags
- `python/context_extractor.py` — batch enrich existing shards with embeddings
- `python/shard_index.py` — rebuild shard index manually
- `python/dedup_json.py` — deduplicate shard content
- `python/rename_shards.py` — bulk rename shard files

---

## In Progress

Partially built or actively being developed.

- **Shard retitling pipeline** — automated renaming of shards to descriptive, canonical titles. Logic is sketched; tooling in `python/rename_shards.py` exists but is not wired to the server.
- **Gemini worker integration** (`mcp/gemini_worker.py`) — Forgemaster lane for Gemini Flash. File exists but is not fully integrated into the orchestration workflow.
- **Autoresearch topic library** — `tools/autoresearch.py` is functional but the topic set is sparse. Expanding to cover all Forgemaster skill domains.
- **Known issue: `nova_shard_create` cold-start timeout** — post-write `enrich_shard` embedding hook stalls on first run before model weights are cached. Workaround pending; tracked in `KNOWN_ISSUES.md`.

---

## Planned

Known future work, not yet started.

- **Shard compaction script** — standalone CLI to compact bloated shards outside the MCP server, for use in offline maintenance or CI pipelines.
- **Theme analyzer pipeline** — automated tagging of shards by semantic theme cluster. Intended to replace manual `theme` field population and enable cross-shard clustering views.
- **Semantic clustering** — group shards by embedding proximity into named clusters. Feeds theme analyzer and merge suggestion ranking.
- **Forgemaster full integration** — wire all Forgemaster skill lanes (orchestrator → implementer → reviewer) into a single runnable workflow with NOVA as the shared context store. Currently skills exist as prompts; automation layer is not yet executable.
- **Stitch MCP integration** — UI/screen design lane for Forgemaster. Currently blocked by auth timeout (tracked in `KNOWN_ISSUES.md #1`).
- **Shard expiry and archival policy** — configurable rules for auto-archiving shards below a confidence floor after a defined period of inactivity.
- **NOVA web UI** — read-only browser interface for browsing shards, graph, and usage log. `game/index.html` is an early prototype.
- **Multi-user shard namespacing** — partition shards by user or project ID for shared deployments.
