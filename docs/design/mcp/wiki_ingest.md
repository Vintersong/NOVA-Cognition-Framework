# wiki_ingest.py

**One-line purpose:** Two-pass ingest pipeline for wiki pages — Haiku routing pass to select relevant slugs, Sonnet synthesis pass to write/update each page.

## Why it exists

Ingesting external documents into a structured wiki naively would require routing and writing logic in the same call, with no separation of concerns. The two-pass design keeps routing cheap (Haiku, short prompt, JSON array output) and synthesis expensive but focused (Sonnet, per-page, full context). Both passes use prompt caching on system prompts to reduce cost when batching multiple sources.

## Key concepts

- **Routing pass** — Haiku + `_ROUTING_SYSTEM` prompt → returns JSON array of relevant slugs from the schema. Falls back to `[]` if API key absent or if no slugs are relevant. Truncates source to first 20,000 chars.
- **Synthesis pass** — Sonnet + `_SYNTHESIS_SYSTEM` prompt → writes or updates a single page body (no frontmatter). Receives: existing page body (if any), other page slugs for cross-reference awareness, source text. Fallback on no API key: raw `*Ingested from: source*\n\n{source[:2000]}`.
- **Prompt caching** — both passes use `"cache_control": {"type": "ephemeral"}` on system prompts. When multiple sources are ingested in sequence, the system prompt hits the cache on turns 2+.
- **Singleton Anthropic client** — `_get_client()` lazy-initialises at first use (reads `CLAUDE_API_KEY` at construction).
- **Index and log maintenance** — after synthesis, `wiki/index.md` is updated via a Haiku call (or fallback markdown append), and `wiki/log.md` gets a timestamped entry appended.
- **Re-embedding** — each updated page's `full_text` is re-embedded via `generate_local_embedding` and upserted into `wiki_index.json`.

## Public surface

- `ingest_source(source_text, source_name, dry_run) → dict` — main entry point. `dry_run=True` runs routing only, no files written.

## Inputs and outputs

- **Reads:** `wiki_schema.json` (via `wiki.schema_summary_lines`); existing wiki page files.
- **Writes:** `wiki/{slug}.md` for each synthesised page; `wiki_index.json`; `wiki/index.md`; `wiki/log.md`.
- **Env:** `CLAUDE_API_KEY`, `WIKI_ROUTING_MODEL`, `WIKI_SYNTHESIS_MODEL`.

## Invariants and assumptions

- `WIKI_ROUTING_MODEL` was `"claude-haiku-3-5"` (retired model) — fixed 2026-04-24 in `config.py` to `claude-haiku-4-5-20251001`. No action needed.
- `ingest_source` requires at least one schema entry — returns `{"status": "no_schema"}` if `schema_summary_lines()` is empty.
- Source text is truncated at 50,000 chars before passing to the pipeline (`nova_wiki_ingest` does the truncation); ingest functions themselves truncate to 20,000 chars in prompt construction.
- Synthesis response is taken verbatim as the page body — no validation that the model followed the `[[wikilink]]` convention or avoided inventing facts.
- `_update_index` failure is silently suppressed (`except Exception: pass`) — index update is non-fatal.

## Callers and integration

- `wiki_tools.py` — `nova_wiki_ingest` tool handler calls `ingest_source`.

## Known gaps / open questions

- `WIKI_ROUTING_MODEL` was a retired model name — fixed 2026-04-24. No further action needed.
- No concurrency protection on wiki file writes — two concurrent `nova_wiki_ingest` calls for the same slug would produce a race condition.
- `_update_index` uses Haiku for index maintenance. If it fails, the index silently falls out of sync with the actual pages.
- `_append_log` opens `log.md` in append mode without a file lock — safe for single-process use only.
