# Code Index (Kilo-style semantic code search) — Design

Date: 2026-08-06
Status: Approved

## Problem

Agents working in this repo currently rely on Grep/Glob to find relevant code.
Nidhogg (`mcp/nidhogg.py`) already builds a semantic index — but of *documents
against shard embeddings*, not of the repo's own source code. There is no way
to ask "find me the function that does X" without knowing the file or an exact
string to grep for. This mirrors the "codebase indexing" feature in Kilo
(Tree-sitter chunking + embeddings + vector search), adapted to NOVA's existing
local-embedding infrastructure.

## Scope

Indexes `mcp/**/*.py` only (the actual server codebase, not `tests/`,
`utilities/`, or `forgemaster/`'s markdown personas/skills). Smallest useful
scope; can be widened later without a design change if it proves useful.

## Architecture

New module `mcp/code_index.py`, parallel to `nidhogg.py` but independent of
the shard graph — it never reads or writes `shards/`. Three parts:

- **Indexer** — walks `mcp/**/*.py`, AST-chunks each file, embeds chunks via
  the existing `generate_local_embedding` (`nova_embeddings_local.py`, local
  all-MiniLM-L6-v2, no API key), writes `code_index_manifest.json`.
- **Query** — loads the manifest, embeds a text query, cosine-ranks all
  chunks, returns top matches with live-read source snippets.
- **Hook** — registered on `NovaHookEvent.SESSION_START` in
  `server_context.py`, fire-and-forget, mirroring `run_nott_in_thread`.

## Chunking

AST-walk each `.py` file using Python's own `ast` module (no new dependency —
this repo is all Python and pins `>=3.11`, so `end_lineno` is reliable).

**Revised post-review (2026-08-06):** the initial implementation only chunked
module-level code that appeared *before* the first top-level def/class, and
embedded every top-level def/class as one chunk regardless of size. An Opus
review caught both as real coverage gaps against the real `mcp/` tree —
`tool_registry.py`'s entire `_REGISTRY` (100 lines, after several top-level
defs) was unindexed, and `register_shard_tools` (924 lines) was embedded from
only its first ~20 lines, making all 12 nested shard-tool handlers
unsearchable. The corrected algorithm:

- Each **top-level** `def`/`class` → one chunk, `kind` = `"function"` or
  `"class"`, `symbol` = its name — **unless** its source exceeds
  `_MAX_CHUNK_CHARS` (800, a character-count proxy for all-MiniLM-L6-v2's
  ~256 word-piece limit), in which case it's split into one chunk per
  immediate child def/method (`kind` still `"function"`/`"class"`, `symbol`
  = dotted qualname, e.g. `register_shard_tools.nova_shard_forget`) plus a
  `"function_header"`/`"class_header"` chunk for whatever's left over
  (recursing further if a child is itself oversized; kept as one
  embed-truncated chunk if there's nothing left to split into).
- Everything else at module level (docstring, imports, constants, top-level
  statements between or after defs) → one or more **module-header** chunks,
  `kind` = `"module_header"`, `symbol` = `"<module>"` (`"<module>#2"`, `"#3"`,
  … for additional runs or size-split windows within a run).
- Any oversized module-header run or leftover header run is windowed by the
  same `_MAX_CHUNK_CHARS` cap (`_split_by_size`), since there's no def/class
  structure to recurse into there.
- Chunk IDs are `"{file}::{symbol}:{start_line}"` — the line-number suffix
  avoids collisions between duplicate top-level names or multiple
  module-header runs, which a bare `"{file}::{symbol}"` ID could not.

## Storage — `code_index_manifest.json`

```json
{
  "version": 1,
  "files": {
    "nidhogg.py": {
      "hash": "sha256:...",
      "chunk_ids": ["nidhogg.py::<module>:1", "nidhogg.py::_ingest_file:362"]
    }
  },
  "chunks": {
    "nidhogg.py::_ingest_file:362": {
      "file": "nidhogg.py",
      "symbol": "_ingest_file",
      "kind": "function",
      "start_line": 362,
      "end_line": 479,
      "embedding": [0.0]
    }
  }
}
```

No source text is stored in the manifest — only line ranges. At query time,
`nova_code_search` reads the live file and slices those lines for the
snippet, so results always reflect current disk content even if the index is
a session or two stale.

## Refresh algorithm (idempotent, incremental)

Per file: SHA256 the whole file, compare to `manifest["files"][path]["hash"]`.

- Unchanged → skip entirely, no re-embed.
- Changed or new → re-chunk, re-embed, replace that file's chunk entries.
- Present in the manifest but gone from disk → delete its entries (handles
  deletes and renames).

Same shape as `nidhogg.py`'s `_file_hash` + manifest-skip pattern, keyed by
file instead of whole-document hash. Manifest writes go through
`atomic_write_json` under a `FileLock`, matching `nidhogg_manifest.json`'s
existing concurrency handling.

## Hook wiring

`server_context.py::_register_default_hooks` gets a fourth handler alongside
`_nott_session_start`:

```python
def run_code_index_refresh_in_thread(self) -> None:
    lock = self._code_index_lock
    def _run() -> None:
        if not lock.acquire(blocking=False):
            return  # a refresh is already running — skip
        try:
            code_index.refresh_code_index()
        finally:
            lock.release()
    threading.Thread(target=_run, daemon=True).start()
```

Fire-and-forget, one refresh at a time via a dedicated lock — identical shape
to `run_nott_in_thread`. There is no manual warm-up tool: the first session
after this ships pays the full-repo embed cost in the background; every
session after that is a fast hash-and-skip pass since nothing changed.

## `nova_code_search` tool

Registered in `code_index.py`'s own `register_code_index_tools(mcp, ctx)`,
called from `nova_server.py` alongside the other `register_*_tools` calls.

- **Input**: `query: str`, `top_n: int = 5` (range 1–20, matching the
  `nova_shard_search` / `nidhogg_ingest` convention).
- **Output** per match: `file`, `symbol`, `kind`, `start_line`, `end_line`,
  `similarity_score`, `source` (the live-read snippet, full chunk body).
- **Capability**: `fs.read`, non-irreversible — a pure local read/compute
  tool, same tier as `nova_shard_get`.

## Error handling

- `sentence-transformers` not installed → `generate_local_embedding` already
  returns `None`; the indexer records `enrichment_status`-style skip and
  `nova_code_search` reports "code index unavailable" rather than crashing,
  matching how `nidhogg_ingest` degrades today.
- A file that fails to parse (`SyntaxError` from `ast.parse`) is skipped and
  logged, not fatal to the whole refresh pass.
- Manifest missing or corrupt on query → treated as empty index (no matches),
  not an error; next SESSION_START hook rebuilds it.

## Testing

`tests/test_code_index.py` (14 tests):

- AST chunker on a small fixture file (one function, one class with methods,
  one module-level constant) → correct chunk boundaries and `kind` labels,
  including the module-header run that falls *between* two top-level defs.
- Module-level code between/after defs gets its own chunk(s), not just the
  leading block (regression test for the post-review chunking fix).
- An oversized top-level function recurses into its nested defs plus a
  header chunk; one with no nested defs to split into is kept whole; an
  oversized module-level run is size-split into contiguous windows
  (regression tests for the post-review size-cap fix).
- Incremental manifest behavior: unchanged file is skipped (no re-embed
  call), changed file is re-embedded and its old chunk entries replaced,
  deleted file's entries are pruned on the next refresh.
- Cosine ranking returns the expected top match on a synthetic manifest with
  mocked embedding vectors (avoids depending on `sentence-transformers`
  actually loading in CI).

No dedicated test for the hook thread-spawning itself, consistent with there
being no such test for `run_nott_in_thread` today.

## Known follow-ups (from the Opus review, not yet addressed)

Medium/low-severity findings that are real but out of scope for the blocking
fix pass above — left for a future session:

- Manifest load→mutate isn't lock-guarded, only the final write is; a
  concurrent refresh (realistic since NOVA can run as a globally-registered
  server) can lose an update.
- `logger` is imported but unused; parse/embed failures aren't logged despite
  the spec calling for it, and `files_failed` conflates syntax errors with
  "embedding model unavailable" into one indistinguishable counter.
- The tool handler does model-load + manifest-parse + cosine synchronously on
  the event loop instead of `run_in_executor`, blocking other MCP calls
  during a refresh.
- `_search_chunks` re-parses the full manifest JSON on every query — no
  mtime-keyed cache.
- Returned snippets use stale line offsets if the file changed since the last
  refresh; no `stale` flag when the source no longer matches.
