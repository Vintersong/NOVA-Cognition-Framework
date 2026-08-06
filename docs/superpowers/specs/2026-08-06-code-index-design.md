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
this repo is all Python and pins `>=3.11`, so `end_lineno` is reliable):

- Each **top-level** `def`/`class` → one chunk, `kind` = `"function"` or
  `"class"`, `symbol` = its name.
- Everything else at module level (docstring, imports, constants, top-level
  statements) → one **module-header** chunk per file, `kind` =
  `"module_header"`, `symbol` = `"<module>"`.

## Storage — `code_index_manifest.json`

```json
{
  "version": 1,
  "files": {
    "nidhogg.py": {
      "hash": "sha256:...",
      "chunk_ids": ["nidhogg.py::<module>", "nidhogg.py::_ingest_file"]
    }
  },
  "chunks": {
    "nidhogg.py::_ingest_file": {
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

`tests/test_code_index.py`:

- AST chunker on a small fixture file (one function, one class with methods,
  one module-level constant) → correct chunk boundaries and `kind` labels.
- Incremental manifest behavior: unchanged file is skipped (no re-embed
  call), changed file is re-embedded and its old chunk entries replaced,
  deleted file's entries are pruned on the next refresh.
- Cosine ranking returns the expected top match on a synthetic manifest with
  mocked embedding vectors (avoids depending on `sentence-transformers`
  actually loading in CI).

No dedicated test for the hook thread-spawning itself, consistent with there
being no such test for `run_nott_in_thread` today.
