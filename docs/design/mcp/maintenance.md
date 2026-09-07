# maintenance.py

**One-line purpose:** Confidence decay, auto-compaction, cosine similarity, and merge-candidate detection — the policy layer for shard health.

## Why it exists

Shard quality degrades over time without intervention: old shards lose relevance, long shards accumulate noise, similar shards fragment related knowledge. `maintenance.py` centralises all health policies so thresholds live in `config.py` and NÓTT (`nott.py`) owns scheduling. Tool handlers never call these functions directly.

## Key concepts

- **Confidence decay** — `MAX(0.1, confidence × (1 - decay_rate))` applied per elapsed `DECAY_INTERVAL_DAYS` period. Floor of 0.1 prevents shards from ever reaching zero.
- **Auto-compaction** — when `conversation_history` length exceeds `COMPACT_THRESHOLD`, older turns are replaced with a structured summary (Goal/Progress/Decisions/Next) from `nova_embeddings_local._generate_compaction_summary`. Only the last `COMPACT_KEEP_RECENT` turns are kept in full.
- **Cosine similarity** — pure Python implementation (no numpy); used by `find_merge_candidates` to compare shard embeddings.
- **Merge candidates** — shards with `context.embedding` set, cosine similarity above `MERGE_SIMILARITY_THRESHOLD`, and not archived. Results sorted descending by similarity score.

## Public surface

- `get_confidence(shard_data) → float` — read `meta_tags.confidence`, default `1.0`.
- `apply_confidence_decay(shard_data) → float` — mutates `meta_tags.confidence` in-place; returns new value.
- `confidence_weighted_score(base_score, confidence) → float` — search relevance blended with confidence.
- `maybe_compact_shard(shard_data, shard_id) → bool` — compact if over threshold; returns True if compaction ran.
- `cosine_similarity(a, b) → float` — vector cosine; returns 0.0 on dimension mismatch.
- `find_merge_candidates(shard_id, shard_data, index) → list[dict]` — returns candidates above `MERGE_SIMILARITY_THRESHOLD`.

## Inputs and outputs

- **Reads:** shard JSON files directly (not via `store.load_shard`) in `find_merge_candidates` — bypasses the file lock.
- **Writes:** mutates the passed-in `shard_data` dict in-place; callers (NÓTT) are responsible for saving to disk.
- **Env:** `COMPACT_THRESHOLD`, `COMPACT_KEEP_RECENT`, `DECAY_RATE`, `DECAY_INTERVAL_DAYS`, `MERGE_SIMILARITY_THRESHOLD` (all via `config.py`).

## Invariants and assumptions

- **[USER NARRATION NEEDED]** `apply_confidence_decay` uses `MAX(0.1, confidence × (1 - decay_rate))` — this is a float-based exponential decay, incompatible with the discrete `{-1, 0, 1}` model in `shard_parser.py`. This function is the primary migration blocker in the decay path.
- `find_merge_candidates` requires `context.embedding` to be present — unenriched shards are skipped entirely and never suggested for merge.
- `cosine_similarity` is duplicated in `store.py` (`wiki_tools.py`) — three copies of the same function exist across the codebase.
- `find_merge_candidates` reads shard files directly with `json.load`, not through `store.load_shard` — no file locking during read.

## Callers and integration

- `nott.py` — all three public mutating functions (`apply_confidence_decay`, `maybe_compact_shard`, `find_merge_candidates`) are injected into `Nott` at construction time as callables.
- `shard_tools.py` — imports `apply_confidence_decay` and `maybe_compact_shard` for inline use alongside the NÓTT passes.
- `nidhogg.py` — imports `cosine_similarity` for document-to-shard matching.

## Known gaps / open questions

- Float decay formula is incompatible with the new discrete confidence model — this is the central blocker for the `shard_parser.py` migration.
- `find_merge_candidates` reads shard files without file locks — race condition if NÓTT runs compaction concurrently.
- The compaction summary template is heuristic (keyword scan for decision-bearing sentences) — quality degrades on technical or non-English content.
