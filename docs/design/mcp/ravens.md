# ravens.py

**One-line purpose:** HUGINN (Haiku fast retrieval) and MUNINN (Sonnet deep rerank) — NOVA's two-pass shard retrieval system.

## Why it exists

Single-pass keyword search over shard metadata degrades at scale and misses semantic relatedness. HUGINN does a fast pre-filter using token-overlap + Jaccard scoring, then optionally calls Haiku to LLM-rescore the candidates. If HUGINN's max confidence is below `HUGINN_CONFIDENCE_THRESHOLD`, MUNINN runs a second pass using cosine similarity against shard embeddings, then optionally Sonnet for deep semantic reranking. Both passes fall back gracefully to local-only scoring when `CLAUDE_API_KEY` is absent.

## Key concepts

- **Two-pass design** — HUGINN always runs; MUNINN runs only when HUGINN is not confident enough. This keeps the common case cheap.
- **`RetrievalResult`** — frozen dataclass returned by both ravens: `shard_ids`, `scores`, `reasoning`, `used_llm`, `max_confidence`, `operator`. `is_confident(threshold)` determines whether to skip MUNINN.
- **XML score format** — both LLM re-scores use `<score id="shard_id" value="0.0–1.0">reason</score>` tags. `_parse_score_xml` is tolerant of extra whitespace, multiline reasoning, and partial output.
- **`_RAVEN_API_TIMEOUT`** — 10s timeout (env `RAVEN_API_TIMEOUT`) on each LLM call via `asyncio.wait_for`. On timeout, local scores are used without error to the caller.
- **Privacy-preserving query log** — `_query_log_metadata` stores SHA256 digest and length, not the query text. `NOVA_LOG_QUERY_PREVIEW=true` enables opt-in preview logging (first 80 chars).

## Public surface

**`Huginn`**:
- `retrieve(query, index, top_n) → RetrievalResult` (async) — local pre-filter (token-overlap + Jaccard blend × confidence × trust) → Haiku LLM rescore if API key available.

**`Muninn`**:
- `rerank(query, candidates, index, top_n) → RetrievalResult` (async) — cosine rerank of HUGINN candidates → Sonnet deep rerank if API key available.

**`_parse_score_xml(raw) → (scores, reasoning)`** — module-level; used by both ravens.

## Inputs and outputs

- **Reads:** shard files (for last 3 turns preview in MUNINN), `shard_index.json` (via the `index` argument passed in).
- **Writes:** usage entries to `nova_usage.jsonl` (append-only via `_log()`).
- **Env:** `CLAUDE_API_KEY`, `HUGINN_MODEL`, `MUNINN_MODEL`, `RAVEN_API_TIMEOUT`, `NOVA_LOG_QUERY_PREVIEW`.

## Invariants and assumptions

- HUGINN local scoring uses `confidence × trust` weighting — `trust_score` in the index entry defaults to `1.0` when absent. Floats assumed throughout; incompatible with discrete confidence.
- MUNINN local rerank blends cosine similarity (0.6) with HUGINN score (0.4). Shards without embeddings are penalised by 20% but still included.
- Both ravens are async and use `asyncio.to_thread` to run blocking LLM calls without blocking the event loop.
- `_local_retrieve` filters out `archived` and `forgotten` tagged shards before scoring — consistent with other retrieval paths.

## Callers and integration

- `nova_server.py` — constructs `Huginn` and `Muninn` singletons at startup; calls them in sequence inside `nova_shard_interact` and `nova_shard_search`.
- No callers outside `mcp/`.

## Known gaps / open questions

- HUGINN local scoring uses `confidence` from the index as a float multiplier — incompatible with the discrete `{-1, 0, 1}` model. A shard with `confidence = -1` would produce negative scores.
- `_cosine` in this file is the third copy of the same function (also in `maintenance.py` and `wiki_tools.py`).
- MUNINN reads shard files directly (not through `store.load_shard`) for the turns preview — no file locking.
