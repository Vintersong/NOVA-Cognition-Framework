# nidhogg.py

**One-line purpose:** Document ingestion pipeline — embed external files, cosine-match against shards, append provenance blocks, and flag merge candidates for NÓTT.

## Why it exists

Shards are created from conversations. External documents (papers, articles, notes, PDFs) contain knowledge that is relevant to existing shards but never naturally enters conversation history. Nidhogg bridges this gap: it reads files, embeds them, finds the closest shards by semantic similarity, runs Haiku structured analysis in context, and appends a non-destructive `nidhogg` block to each matched shard. The original shard fields are never modified.

## Key concepts

- **SHA256 manifest** (`nidhogg_manifest.json`) — idempotent re-ingestion guard. A file already in the manifest at its current hash is skipped without scanning.
- **Path allowlist** (`NIDHOGG_ALLOWED_ROOTS`) — resolved from `NIDHOGG_ALLOWED_ROOTS` env var (comma-separated). Defaults to `intake/`. Any path outside the allowlist is rejected with `code: "path_not_allowed"`.
- **Mean-pool embedding** — long documents are chunked at paragraph boundaries (max 4000 chars/chunk), each chunk is embedded, and the chunks are averaged into a single document embedding.
- **`nidhogg` block** — appended to `shard_data["nidhogg"]` list: source file, hash, source type, ingestion timestamp, similarity score, `merge_candidate` flag, and extracted `{entities, concepts, relationships, contradictions, summary}` from Haiku.
- **Merge candidate flag** — `similarity_score >= MERGE_SIMILARITY_THRESHOLD` (default 0.85) sets `merge_candidate: True` in the block; NÓTT reads this flag during its merge pass.
- **`NIDHOGG_SIMILARITY_THRESHOLD`** — default 0.55 (env `NIDHOGG_SIMILARITY_THRESHOLD`). Lower than NOVA's merge threshold — documents are matched to shards at moderate similarity; only high-similarity matches are flagged for merge.

## Public surface

- `register_nidhogg_tools(mcp)` — registers 3 tools onto an MCPServer instance.

**MCP tools** (3):
- `nidhogg_ingest` — ingest a single file by path.
- `nidhogg_scan` — scan `intake/` directory and ingest all pending files.
- `nidhogg_status` — show manifest summary.

## Inputs and outputs

- **Reads:** files from `NIDHOGG_ALLOWED_ROOTS`; shard JSON files for embeddings; `shard_index.json`.
- **Writes:** `nidhogg_manifest.json`; appends to matched shard JSON files (via `store.save_shard`, file-locked).
- **Env:** `NIDHOGG_INTAKE_DIR`, `NIDHOGG_MANIFEST_FILE`, `NIDHOGG_SIMILARITY_THRESHOLD`, `NIDHOGG_ALLOWED_ROOTS`, `CLAUDE_API_KEY` (for Haiku analysis), `HUGINN_MODEL` (used as Haiku model alias).

## Invariants and assumptions

- Never modifies existing shard fields — only appends to the `nidhogg` list.
- PDF support requires `pypdf` (`pip install pypdf`) — produces a graceful error message if absent.
- Haiku analysis is skipped entirely (returns `{"analysis_skipped": "no CLAUDE_API_KEY"}`) when `CLAUDE_API_KEY` is absent. The nidhogg block is still written with empty extracted fields.
- Haiku analysis parses the response as raw `json.loads(raw)` — if Haiku returns non-JSON the block gets `{"analysis_error": "..."}` instead.
- `_match_shards` iterates every shard in the index and opens each shard file to read `context.embedding` — O(n) file reads per ingestion. Fine at small scale; will slow significantly with hundreds of shards.

## Callers and integration

- `nova_server.py` — calls `register_nidhogg_tools(mcp)` at startup.

## Known gaps / open questions

- `_match_shards` reads shard JSON files directly via `json.load` (opens each shard file to read `context.embedding`) — bypasses `shard_parser.py`. Migration to the new `.shard` format will require updates here. Raw JSON bypass.
- `_match_shards` reads shard files directly without file locking — concurrent NÓTT compaction could read a shard mid-write.
- `similarity_score` written into the `nidhogg` block is a float match score (separate from shard confidence) — not a migration blocker on its own, but any downstream code that reads this field will need clarity on its semantics post-migration.
- No batch Haiku call — one API call per matched shard. For `top_n=5`, each ingestion triggers 5 Haiku calls sequentially.
- The `intake/` directory is not created automatically by the server at startup — `nidhogg_scan` creates it via `os.makedirs` but `nidhogg_ingest` does not. Pointing `nidhogg_ingest` at a file outside `intake/` is the primary usage path anyway.
