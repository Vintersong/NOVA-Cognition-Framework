# wiki_tools.py

**One-line purpose:** MCP tool handlers for the wiki layer — schema management, ingest, semantic search, read, list, and lint.

## Why it exists

`wiki.py` and `wiki_ingest.py` provide the backend; `wiki_tools.py` wraps them in the MCPServer registration pattern consistent with all other tool modules (`nidhogg.py`, `evolve.py`, `gemini_mcp.py`). All 6 wiki tools are registered in a single `register_wiki_tools(mcp)` call.

## Key concepts

- **`nova_wiki_query`** — semantic cosine search over `wiki_index.json` embeddings. Falls back to keyword count search if no embedding index is available. Generates query embedding via `generate_local_embedding`.
- **`nova_wiki_lint`** — four health checks: orphan pages (no inbound `[[wikilinks]]`), broken links (target slug not in wiki), missing embeddings (slug not in index), stale pages (not updated in 30+ days). `deep=True` adds an LLM contradiction check across all pages via Sonnet.
- **`_deep_lint`** — builds a compact summary of all pages (title + first 300 chars each) and asks Sonnet to identify factual contradictions; returns `{slug: [contradictions]}`. One API call for all pages — expensive but bounded.
- **`nova_wiki_schema`** — three actions: `get` (return schema JSON), `add` (append new page spec), `remove` (remove spec by slug; does not delete the wiki file).
- **`_cosine`** — local cosine similarity; third copy of the same function in the codebase (also in `maintenance.py` and `ravens.py`).
- **`_excerpt`** — returns 200-char window around first query occurrence; falls back to first 200 chars of body.

## Public surface

- `register_wiki_tools(mcp)` — registers all 6 wiki tools onto an MCPServer instance.

**MCP tools** (6): `nova_wiki_schema`, `nova_wiki_ingest`, `nova_wiki_query`, `nova_wiki_get`, `nova_wiki_list`, `nova_wiki_lint`.

## Inputs and outputs

- **Reads:** wiki page files, `wiki_index.json`, `wiki_schema.json`.
- **Writes:** `wiki_schema.json` (schema add/remove); delegates writes to `wiki_ingest.ingest_source`.
- **Env:** `CLAUDE_API_KEY` (for `_deep_lint` and ingest synthesis).

## Invariants and assumptions

- `nova_wiki_query` falls back to keyword search when `load_wiki_index()` returns an empty dict — this covers the case where no pages have been embedded yet.
- `nova_wiki_list` `category` filter is exact string match — no partial matching.
- `nova_wiki_lint` `stale` check handles missing `tzinfo` on `updated` by assuming UTC; pages written before timezone-aware datetimes were introduced are handled correctly.
- `nova_wiki_schema remove` does not delete the wiki file — orphan wiki files remain on disk. They will appear in `nova_wiki_list` but will not be updated by subsequent ingests (routing checks schema).

## Callers and integration

- `nova_server.py` — calls `register_wiki_tools(mcp)` at startup.

## Known gaps / open questions

- `_cosine` is the third copy of the same function (`maintenance.py`, `ravens.py`, `wiki_tools.py`) — should be extracted to a shared utility.
- `nova_wiki_lint` `stale` threshold is hardcoded at 30 days — not configurable via env var.
- `_deep_lint` sends the first 300 chars of each page body — may not capture contradictions buried deeper in long pages.
- `nova_wiki_schema remove` leaves orphan wiki files. A `delete=True` flag that also removes the `.md` file would prevent phantom pages.
