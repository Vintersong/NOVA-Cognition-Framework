# wiki.py

**One-line purpose:** `WikiPage` model, CRUD helpers, embedding index, and wiki schema management for NOVA's curated knowledge layer.

## Why it exists

Shards are ephemeral conversation records with confidence decay. The wiki layer holds curated, evergreen reference pages synthesised from external sources — structured markdown files with YAML frontmatter. `wiki.py` defines the data model, file format, and the embedding index (`wiki_index.json`) that powers semantic search over wiki pages.

## Key concepts

- **`WikiPage`** — dataclass: `slug`, `title`, `tags`, `updated`, `sources`, `body`, `category`. Frontmatter written as a controlled YAML subset by `to_file()`; parsed by `from_file()`.
- **Frontmatter format** — `---\ntitle: ...\nslug: ...\n...\n---\n\nbody`. Custom `_parse_frontmatter` handles inline lists `[a, b]` and block lists (`  - item`). Does not use PyYAML — avoids the full YAML surface.
- **`[[wikilinks]]`** — `_LINK_RE` extracts `[[slug]]` references from page bodies; `outbound_links` property returns them. Used by `nova_wiki_lint` for broken link detection.
- **Wiki embedding index** — `wiki_index.json` maps `slug → {title, embedding, updated}`. No frontmatter — embeddings make the file large; written without indent.
- **`WikiPageSpec`** — the schema entry: `slug`, `title`, `description`, `tags`, `category`. Stored in `wiki_schema.json`. The schema defines what pages exist; actual `.md` files are created by ingest.

## Public surface

- `WikiPage.from_file(path)` / `WikiPage.to_file(path)` — read/write `.md` with frontmatter.
- `WikiPage.full_text` — `# title\n\nbody` — the unit embedded by `nova_embeddings_local`.
- `WikiPage.outbound_links` — list of `[[slug]]` targets.
- `load_wiki_page(slug) → Optional[WikiPage]`
- `all_wiki_pages() → list[WikiPage]` — skips `index.md` and `log.md`.
- `wiki_dir_ready() → Path` — ensures `WIKI_DIR` exists.
- `load_wiki_index() / save_wiki_index(index) / upsert_wiki_embedding(slug, title, embedding) / remove_wiki_embedding(slug)` — embedding index management.
- `load_wiki_schema() / save_wiki_schema(pages) / schema_by_slug(slug) / schema_summary_lines()` — schema management.

## Inputs and outputs

- **Reads/writes:** `WIKI_DIR/*.md`, `WIKI_INDEX_FILE` (`wiki_index.json`), `WIKI_SCHEMA_FILE` (`wiki_schema.json`).
- **Env:** `NOVA_WIKI_DIR`, `NOVA_WIKI_INDEX_FILE`, `NOVA_WIKI_SCHEMA_FILE` (via `config.py`).

## Invariants and assumptions

- `all_wiki_pages()` excludes `index.md` and `log.md` by stem — these are management files maintained by `wiki_ingest.py`.
- `_parse_frontmatter` only handles the controlled subset written by `to_file()` — arbitrary YAML (nested dicts, multi-line strings, anchors) will be silently mis-parsed or ignored.
- Wiki pages do not decay — they are replaced wholesale when outdated via a new ingest pass.
- `category` defaults to `"general"` on both `WikiPage` and `WikiPageSpec` — most real pages will need an explicit category for the `nova_wiki_list` category filter to be useful.

## Callers and integration

- `wiki_ingest.py` — uses `WikiPage`, `load_wiki_page`, `all_wiki_pages`, `wiki_dir_ready`, `schema_*`, `upsert_wiki_embedding`.
- `wiki_tools.py` — uses all of the above plus `load_wiki_index`.
- `nova_server.py` — imports `register_wiki_tools` from `wiki_tools.py`, not from `wiki.py` directly.

## Known gaps / open questions

- No file locking on wiki page writes (`to_file` uses `Path.write_text` directly) — concurrent ingest of two sources updating the same slug would produce last-write-wins corruption.
- `save_wiki_index` writes without indent — the file is human-unreadable. A compact pretty-print option would help debugging.
- `WikiPageSpec.description` is stored in the schema but not surfaced in wiki page frontmatter — it exists only for routing context in `wiki_ingest.py`. Pages and schema can diverge if a spec is edited after the page is written.
