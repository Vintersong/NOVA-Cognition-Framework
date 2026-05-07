# nova_embeddings_local.py

**One-line purpose:** Local all-MiniLM-L6-v2 embedding backend — generates 384-dim vectors and heuristic compaction summaries without an API key.

## Why it exists

All retrieval and merge detection in NOVA depends on semantic embeddings. `nova_embeddings_local.py` provides the embedding layer using `sentence-transformers` running entirely on CPU, so the system degrades gracefully when `CLAUDE_API_KEY` is absent rather than failing hard. The model is loaded once at server startup and reused for all subsequent operations.

## Key concepts

- **all-MiniLM-L6-v2** — 80MB, 384-dimensional, Apache 2.0. Downloaded to `~/.cache/huggingface/` on first run; all subsequent runs are offline.
- **Pre-warm** — `prewarm_embedding_model()` starts loading in a daemon background thread at server startup so the first tool call never blocks waiting for weights.
- **Thread-safe singleton** — `_model_lock` (a `threading.Lock`) ensures the model is loaded exactly once under concurrent access.
- **Compaction summary template** — `[GOAL] / [PROGRESS] / [DECISIONS] / [NEXT]` structure borrowed from hermes-agent `ContextCompressor`. Heuristic: scans AI messages for decision-bearing keywords (`decided`, `will use`, `chosen`, etc.); no LLM required.

## Public surface

- `get_embedding_model() → SentenceTransformer | None` — thread-safe lazy load; returns `None` and prints a warning if `sentence-transformers` is not installed.
- `prewarm_embedding_model() → None` — fire-and-forget daemon thread; called by `nova_server.py` at startup.
- `generate_local_embedding(text) → list[float] | None` — encode text; returns `None` if model unavailable.
- `enrich_shard(shard_id, shard_data)` — post-write hook: generates embedding + keyword topics; sets `context` dict and `meta_tags.enrichment_status`. Blocking — not async.
- `_generate_compaction_summary(turns, shard_id) → str` — called by `maintenance.maybe_compact_shard`; delegates to `generate_local_summary`.

## Inputs and outputs

- **Reads:** shard `conversation_history` (last 5 turns) and `guiding_question` for embedding input.
- **Writes:** mutates `shard_data["context"]` and `shard_data["meta_tags"]["enrichment_status"]` in-place. Does not write to disk — caller is responsible for saving.
- **Env:** none consumed directly. `sentence-transformers` must be installed separately.

## Invariants and assumptions

- `enrich_shard` is blocking — it runs on the calling thread, not in a background executor. For high-frequency creates/updates this adds latency to tool responses.
- The keyword extractor in `enrich_shard` uses a fixed stopword list (26 words) — technical jargon and domain terms are extracted correctly but common English words are not reliably filtered.
- `context.summary` is set to `guiding_question` verbatim, not a derived summary — this means the `summary` field in the context is not independent of the guiding question.
- If the model is not installed, `enrichment_status` is set to `"pending"` — these shards are excluded from merge candidate detection since they have no embedding.

## Callers and integration

- `nova_server.py` — calls `prewarm_embedding_model()` at startup; calls `enrich_shard` as post-write hook in `nova_shard_create` and `nova_shard_update`.
- `maintenance.py` — calls `_generate_compaction_summary` via `maybe_compact_shard`.
- `ravens.py` — calls `generate_local_embedding` for MUNINN's local cosine rerank fallback.
- `nidhogg.py` — calls `generate_local_embedding` for document embedding before shard matching.
- `wiki_ingest.py` — calls `generate_local_embedding` to re-embed wiki pages after synthesis.

## Known gaps / open questions

- `enrich_shard` is synchronous (blocking) — should be refactored to an async post-write hook or offloaded to NÓTT's thread pool. The module-level docstring acknowledges this: "Blocking — refactor to async post-write hook in future iteration."
- No mechanism to re-enrich shards that were created before the embedding model was installed (`enrichment_status = "pending"` shards are never retried automatically).
