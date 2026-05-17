# NOVA Configuration Reference

> **Where defaults live:** most variables are centralized in `mcp/config.py` — changing a default there changes it everywhere. A small number of module-local variables (`NOVA_LOG_QUERY_PREVIEW`, `RAVEN_API_TIMEOUT`, `NIDHOGG_SIMILARITY_THRESHOLD`, `FORGEMASTER_EVENT_LOG`, `NOVA_DENIED_TOOLS`, `NOVA_DENIED_PREFIXES`) are read directly from the environment by the module that uses them; each is listed in its themed table below with the consuming module named.
>
> Set variables in your `.env` file at the repo root, or pass them directly in your `claude_desktop_config.json` / Docker `--env` flags.

---

## Critical — Required for full functionality

| Variable | Default | Notes |
|---|---|---|
| `CLAUDE_API_KEY` | *(unset)* | Powers HUGINN, MUNINN, wiki routing/synthesis, and summary index generation. If absent, HUGINN and MUNINN fall back to local-only retrieval silently. |
| `GEMINI_API_KEY` | *(unset)* | Required for `gemini_execute_ticket` and `gemini_load_file`. Forgemaster's implementation lane is unavailable without this. |
| `NOVA_EMBEDDING_HMAC_KEY` | *(unset)* | Hex or UTF-8 secret for HMAC-SHA256 embedding signing (VectorPin). If unset, embedding signing is skipped and integrity checks do not run. Set this in production to protect against tampered vectors. |

---

## Paths

All paths default to subdirectories of the repo root. Override when running multiple NOVA instances or when the repo is not writable.

| Variable | Default | Impact |
|---|---|---|
| `NOVA_SHARD_DIR` | `shards/` | Directory containing all shard JSON files. Changing this switches the active shard store — useful for separating project workspaces. |
| `NOVA_INDEX_FILE` | `shard_index.json` | Fast-browse index rebuilt by `nova_shard_index`. Delete and rebuild if it drifts out of sync. |
| `NOVA_GRAPH_FILE` | `shard_graph.json` | Inter-shard knowledge graph. Delete to reset all relations (they can be rebuilt via `nova_graph_relate`). |
| `NOVA_SUMMARY_INDEX_FILE` | `summary_index.json` | Batch-built summary index used by three-tier discovery. Rebuilt by `build_summary_index.py`. |
| `NOVA_SUMMARY_MARKDOWN_FILE` | `summary_index.md` | Markdown mirror of the summary index. |
| `NOVA_USAGE_LOG` | `nova_usage.jsonl` | JSONL operation log. Never commit; rotate freely. |
| `NOVA_SESSION_STORE_DIR` | `nova_sessions/` | Flushed MCP session state. Sessions are written here by `nova_session_flush` and read by `nova_session_load`. |
| `NOVA_ACCESS_LOG` | `shard_access.jsonl` | Per-shard access log used by the decay-on-read pass. |
| `NOVA_EMBEDDING_INTEGRITY_LOG` | `embedding_integrity.jsonl` | Adversarial embedding event log. Each entry records a shard ID, timestamp, and signature mismatch detail. Never commit. |
| `NOVA_SKILL_AUDIT_LOG` | `skill_audit.db` | SQLite HITL audit log — four-state lifecycle for irreversible tool calls. |
| `NOVA_FACTS_DIR` | `facts/` | Directory of curated `.shard` files for the SQLite facts pre-filter. |
| `NOVA_FACTS_INDEX_FILE` | `facts_index.db` | SQLite index over the facts corpus. Rebuilt by `nova_facts_rebuild`. |
| `NOVA_WIKI_DIR` | `wiki/` | Curated markdown wiki pages with YAML frontmatter. Never edit directly — use `nova_wiki_ingest`. |
| `NOVA_WIKI_SCHEMA` | `wiki_schema.json` | Schema file for wiki page validation. |
| `NOVA_WIKI_INDEX` | `wiki_index.json` | Embedding index over wiki pages. |

---

## Retrieval Tuning

Controls the HUGINN → MUNINN → Spreading Activation retrieval pipeline.

| Variable | Default | Impact |
|---|---|---|
| `HUGINN_MODEL` | `claude-haiku-4-5-20251001` | Model for the fast HUGINN retrieval pass. Swap to a newer Haiku model to improve speed without increasing cost significantly. |
| `MUNINN_MODEL` | `claude-sonnet-4-6` | Model for the deep MUNINN rerank pass. Downgrading reduces rerank quality; upgrading increases cost per retrieval. |
| `HUGINN_CONFIDENCE_THRESHOLD` | `0.7` | HUGINN scores at or above this skip MUNINN entirely. Raise to call MUNINN less often (cheaper, slightly lower quality); lower to call MUNINN more aggressively. |
| `RAVEN_API_TIMEOUT` | `10` | Per-call LLM timeout in seconds. On timeout the MUNINN step is skipped and the HUGINN local-pass result is returned. Increase for slow networks; decrease to fail fast. Read directly by `ravens.py`. |
| `NOVA_MAX_FRAGMENTS` | `10` | Maximum shard fragments injected into context per `nova_shard_interact` call. Raise for richer context; lower to save tokens. |
| `NOVA_AGENT_INFERENCE_WEIGHT` | `0.7` | Score multiplier applied to `agent_inference` shards. Values below `1.0` deprioritise agent-generated content relative to external sources. Set to `1.0` to treat all sources equally. |
| `NOVA_ACTIVATION_MIN_EDGES` | `10` | Minimum graph edge count before spreading activation runs as the third retrieval pass. Below this the graph is too sparse for meaningful propagation. Raise if spreading activation adds noise on small graphs. |
| `NOVA_RECALL_CACHE_TTL` | `300` | In-memory recall cache TTL in seconds. Increase to reduce redundant retrieval calls within a session; decrease for fresher results. |
| `NOVA_LOG_QUERY_PREVIEW` | *(unset)* | Set to `1`/`true` to log the first 200 chars of each HUGINN query to stdout. Useful for debugging retrieval. Read directly by `ravens.py`. |

---

## Maintenance & Decay

Controls the NÓTT daemon's scheduled compaction, decay, and merge passes.

| Variable | Default | Impact |
|---|---|---|
| `NOVA_COMPACT_THRESHOLD` | `30` | Conversation turns in a shard before auto-compaction triggers. Lower values keep shards leaner but compact more aggressively, potentially losing nuance. |
| `NOVA_COMPACT_KEEP` | `15` | Recent turns retained after compaction. The rest are summarised and dropped. Raise if important context is being lost; lower to save storage. |
| `NOVA_DECAY_RATE` | `0.05` | Confidence multiplied by `(1 - DECAY_RATE)` per decay interval. Raising speeds up forgetting; lowering makes memories more persistent. Combined effect over one year at default: ~`0.95^52 ≈ 0.07`. |
| `NOVA_DECAY_DAYS` | `7` | Days per decay interval. Raising slows decay; lowering accelerates it. |
| `NOVA_MERGE_THRESHOLD` | `0.85` | Cosine similarity floor for NÓTT merge suggestions. Lower to surface more merge candidates; raise to only suggest near-duplicates. |
| `NOVA_ADVERSARIAL_TOP_N` | `10` | Number of highest-confidence shards checked per adversarial NÓTT run. Raise to audit more shards per cycle at higher LLM cost. |
| `NOVA_ADVERSARIAL_INTERVAL_DAYS` | `7` | Minimum days between adversarial runs. Raise to run less frequently; lower for more aggressive contradiction detection. |
| `NOTT_COUNT_THRESHOLD` | `100` | Shard count that triggers a NÓTT merge scan (COUNT_THRESHOLD trigger level). Raise if merge scans are too frequent on large stores. |

---

## Decay on Read

Penalises shards that are retrieved frequently without being corroborated by new information.

| Variable | Default | Impact |
|---|---|---|
| `NOVA_DECAY_ON_READ_THRESHOLD` | `5` | Number of retrievals within the window before a penalty is applied. Lower to penalise frequently-retrieved-but-uncorroborated shards sooner. |
| `NOVA_DECAY_ON_READ_WINDOW_DAYS` | `7` | Rolling window in days for counting retrievals. |
| `NOVA_DECAY_ON_READ_PENALTY` | `0.05` | Confidence deducted per NÓTT pass when the threshold is exceeded. Raise to more aggressively demote stale high-traffic shards. |

---

## Confidence & Age Classification

Controls automatic tag assignment on shards.

| Variable | Default | Impact |
|---|---|---|
| `NOVA_CONFIDENCE_LOW` | `0.4` | Shards with confidence below this are tagged `low_confidence` and excluded from default search. Use `include_low_confidence=True` to recall them deliberately. |
| `NOVA_RECENT_DAYS` | `3` | Shards accessed within this many days receive the `recent` tag. |
| `NOVA_STALE_DAYS` | `14` | Shards not accessed within this many days receive the `stale` tag. |

---

## Quarantine

New shards extracted from session context (`session_extracted` source) are held in quarantine before entering the active recall pool.

| Variable | Default | Impact |
|---|---|---|
| `NOVA_QUARANTINE_HOURS` | `48` | Hours a quarantined shard is suppressed. Increase for stricter review periods; lower to admit new shards to search faster. |
| `NOVA_QUARANTINE_PENALTY` | `0.5` | Retrieval score multiplier during quarantine. `0.5` means a quarantined shard needs twice the normal relevance to appear in results. |

---

## Wiki Layer

| Variable | Default | Impact |
|---|---|---|
| `NOVA_WIKI_ROUTING_MODEL` | `claude-haiku-4-5-20251001` | Model used to classify and route wiki ingest jobs. |
| `NOVA_WIKI_SYNTHESIS_MODEL` | `claude-sonnet-4-6` | Model used to synthesise wiki page content during ingest. Downgrade to reduce cost; upgrade for higher-quality synthesis. |

---

## Facts Corpus

The facts layer uses discrete `{-1, 0, 1}` confidence — distinct from the float-confidence shard store.

| Variable | Default | Impact |
|---|---|---|
| `NOVA_FACTS_DIR` | `facts/` | Directory of curated `.shard` files. Add new `.shard` files here and run `nova_facts_rebuild` to index them. |
| `NOVA_FACTS_INDEX_FILE` | `facts_index.db` | SQLite index file. Delete and rebuild with `nova_facts_rebuild` if it becomes stale. |

---

## Gemini / Forgemaster

| Variable | Default | Impact |
|---|---|---|
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model used for the implementation lane. Swap to a newer Flash model to pick up capability improvements. |
| `FORGEMASTER_ORCHESTRATOR_MODEL` | inherits `MUNINN_MODEL` | Model used by the orchestrator role (task decomposition + ticket routing). Defaults to Sonnet. Set to an Opus model alias on high-stakes sprints where mis-routing a ticket would cascade into many review iterations downstream — orchestrator output multiplies across every downstream ticket, so the upgrade is high-leverage spend. |
| `FORGEMASTER_PLANNER_MODEL`      | inherits `MUNINN_MODEL` | Model used by the planner role (expanding the orchestrator's decomposition into concrete steps). Defaults to Sonnet. Opus pays off less here than at the orchestrator, but is the right choice when the planner is the bottleneck on ambiguous specs. |
| `FORGEMASTER_REVIEWER_MODEL`     | inherits `MUNINN_MODEL` | Model used by the reviewer role (spec + quality review). Defaults to Sonnet. Set to an Opus model alias when catching subtle correctness issues matters more than throughput — e.g. before a release cut. |
| `FORGEMASTER_IMPLEMENTER_MODEL`  | inherits `GEMINI_MODEL` | Model used by the implementer role. Defaults to Gemini Flash. Override only when you specifically want an Anthropic model writing code (e.g. languages/frameworks where Flash underperforms). The `_dispatch` provider detection routes `claude-*` IDs to Anthropic and `gemini-*` IDs to Google automatically, so cross-provider overrides work without extra wiring. |
| `FORGEMASTER_EVENT_LOG` | *(unset)* | Override path for the sprint JSONL event log. Defaults to `output/forgemaster_runs/<sprint_id>.jsonl` (read directly by `forgemaster_runtime.py`). |

---

## Security & HITL

| Variable | Default | Impact |
|---|---|---|
| `NOVA_HITL_BROKER` | `interactive` | `interactive` — prompts on Unix/Windows terminal before irreversible tool calls. `policy` — always-deny without prompting (used in Docker and CI where there is no terminal). |
| `NOVA_HITL_TIMEOUT_S` | `30` | Seconds to wait for a human decision before auto-denying in interactive mode. |
| `NOVA_SKILL_AUDIT_LOG` | `skill_audit.db` | SQLite file for the four-state HITL lifecycle (irreversible.request / decision / executed / capability.denied) and post-session biconditional audit. |
| `NOVA_DENIED_TOOLS` | *(unset)* | Comma-separated tool names to block at runtime (e.g. `nova_evolve,nova_shard_forget`). Takes effect immediately without a server restart. Read directly by `permissions.py`. |
| `NOVA_DENIED_PREFIXES` | *(unset)* | Comma-separated tool name prefixes to block (e.g. `gemini_` to disable all Gemini tools). Read directly by `permissions.py`. |

---

## Nidhogg Ingestion

| Variable | Default | Impact |
|---|---|---|
| `NIDHOGG_INTAKE_DIR` | `intake/` | Drop zone for `nidhogg_scan`. Files placed here are ingested on the next scan. |
| `NIDHOGG_MANIFEST_FILE` | `nidhogg_manifest.json` | SHA256 manifest of ingested files — makes ingest idempotent. Delete to force re-ingest of all files. |
| `NIDHOGG_SIMILARITY_THRESHOLD` | `0.55` | Cosine similarity threshold for matching an ingested file to an existing shard for annotation. Lower to annotate more shards; raise to only annotate close matches. Read directly by `nidhogg.py`. |

---

## Misc

| Variable | Default | Impact |
|---|---|---|
| `NOVA_PROJECT_CONTEXT` | *(unset)* | When set, shards whose `project_context` field does not include this value are excluded from retrieval. Useful for isolating a multi-project NOVA instance to a single project per session. |

---

## Internal Constants (not user-settable via environment)

| Constant | Value | Notes |
|---|---|---|
| `SESSION_ID_PATTERN` | `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$` | Regex that session IDs must match (enforced at input validation). Hardcoded in `config.py` — not an env var. IDs are persisted as filenames; this keeps them portable across filesystems. |
