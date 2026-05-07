"""
wiki_ingest.py — Ingest pipeline for NOVA's wiki layer.

Two-pass pipeline per source document:
  1. Routing pass (Haiku)   — which wiki slugs are relevant to this source?
  2. Synthesis pass (Sonnet) — write or update each relevant wiki page

Both passes use prompt caching on system prompts (reused across the batch).
Synthesis is aware of existing page content and cross-references other pages.

After synthesis, each updated page is re-embedded using the local all-MiniLM-L6-v2
model and the wiki_index.json is updated.

wiki/index.md and wiki/log.md are maintained automatically.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from config import (
    CLAUDE_API_KEY,
    WIKI_ROUTING_MODEL,
    WIKI_SYNTHESIS_MODEL,
)
from wiki import (
    WikiPage,
    WikiPageSpec,
    load_wiki_page,
    all_wiki_pages,
    schema_by_slug,
    schema_summary_lines,
    upsert_wiki_embedding,
    wiki_dir_ready,
)
from nova_embeddings_local import generate_local_embedding


_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    return _client


# ═══════════════════════════════════════════════════════════
# PROMPTS
# ═══════════════════════════════════════════════════════════

_ROUTING_SYSTEM = """\
You are a knowledge router for a structured wiki. Given a wiki schema and a source document, \
identify which wiki pages can be meaningfully updated with information from this source.

Return ONLY a JSON array of slugs from the schema. Example: ["slug-a", "slug-b"]
If no pages are relevant, return: []
Do not include slugs that would receive only trivial or tangential updates."""

_SYNTHESIS_SYSTEM = """\
You are a wiki author maintaining a structured personal knowledge base. \
Write or update a wiki page by synthesizing information from a source document.

Rules:
- Write clean, structured markdown with a brief summary paragraph at the top
- Integrate new information with existing content — preserve what is already correct
- Use [[slug]] notation to cross-reference other wiki pages when genuinely relevant
- Be precise and factual — only include information present in the source
- Do not invent, speculate, or pad with filler text
- Return ONLY the page body (no frontmatter, no title heading)"""

_INDEX_SYSTEM = """\
You maintain a wiki index file. Given the current index and a list of updated pages, \
produce an updated markdown table with columns: Page | Summary | Category | Updated.
Sort alphabetically by page title. Return ONLY the complete updated markdown — no preamble."""


def _routing_user(schema_lines: list[str], source_name: str, source_text: str) -> str:
    schema_block = "\n".join(schema_lines) if schema_lines else "  (no pages defined)"
    return (
        f"Source document: {source_name}\n\n"
        f"Wiki schema:\n{schema_block}\n\n"
        f"Source text (first 20000 chars):\n{source_text[:20_000]}"
    )


def _synthesis_user(
    spec: WikiPageSpec,
    existing_body: str | None,
    source_name: str,
    source_text: str,
    other_pages: list[str],
) -> str:
    existing_block = (
        f"Existing page content:\n{existing_body}\n\n" if existing_body
        else "This is a new page — no existing content.\n\n"
    )
    other_block = (
        "Other wiki pages (for cross-reference awareness):\n" + "\n".join(other_pages) + "\n\n"
        if other_pages else ""
    )
    return (
        f"Page to write/update:\n"
        f"  Slug: {spec.slug}\n"
        f"  Title: {spec.title}\n"
        f"  Description: {spec.description}\n\n"
        f"{existing_block}"
        f"{other_block}"
        f"Source document: {source_name}\n\n"
        f"Source text (first 20000 chars):\n{source_text[:20_000]}"
    )


def _index_user(current_index: str, updated_pages: list[dict]) -> str:
    pages_block = json.dumps(updated_pages, indent=2)
    return (
        f"Current index:\n{current_index or '(empty)'}\n\n"
        f"Updated pages:\n{pages_block}"
    )


# ═══════════════════════════════════════════════════════════
# INGEST PIPELINE
# ═══════════════════════════════════════════════════════════

def ingest_source(
    source_text: str,
    source_name: str,
    dry_run: bool = False,
) -> dict:
    """
    Run the full ingest pipeline for a single source.

    Returns a result dict with:
      routed_slugs, synthesized, skipped, dry_run
    """
    wiki_dir_ready()
    schema_lines = schema_summary_lines()

    if not schema_lines:
        return {
            "status":        "no_schema",
            "message":       "Wiki schema is empty. Add pages with nova_wiki_schema first.",
            "routed_slugs":  [],
            "synthesized":   [],
            "dry_run":       dry_run,
        }

    result = {
        "source_name":  source_name,
        "routed_slugs": [],
        "synthesized":  [],
        "skipped":      [],
        "dry_run":      dry_run,
    }

    # ── Step 1: Route ─────────────────────────────────────────────────────────
    if dry_run:
        result["message"] = "Dry run: routing pass only, no files written."
        relevant_slugs = _route(schema_lines, source_name, source_text)
        result["routed_slugs"] = relevant_slugs
        return result

    relevant_slugs = _route(schema_lines, source_name, source_text)
    result["routed_slugs"] = relevant_slugs

    if not relevant_slugs:
        result["message"] = "No relevant wiki pages found for this source."
        return result

    # Build cross-reference context: other pages not being updated
    all_specs  = [spec for slug in relevant_slugs if (spec := schema_by_slug(slug))]
    all_schema = [f"  {s.slug}: {s.title}" for s in _load_all_schema_specs()]
    other_pages = [
        line for line in all_schema
        if not any(slug in line for slug in relevant_slugs)
    ]

    updated_summaries: list[dict] = []

    # ── Step 2: Synthesize each relevant page ─────────────────────────────────
    for slug in relevant_slugs:
        spec = schema_by_slug(slug)
        if spec is None:
            result["skipped"].append({"slug": slug, "reason": "not in schema"})
            continue

        existing = load_wiki_page(slug)
        existing_body = existing.body if existing else None

        new_body = _synthesize(
            spec        = spec,
            existing_body = existing_body,
            source_name   = source_name,
            source_text   = source_text,
            other_pages   = other_pages,
        )

        # Merge provenance
        prev_sources = existing.sources if existing else []
        sources = list(dict.fromkeys(prev_sources + [source_name]))

        page = WikiPage(
            slug     = slug,
            title    = spec.title,
            tags     = spec.tags,
            category = spec.category,
            updated  = datetime.now(timezone.utc),
            sources  = sources,
            body     = new_body,
        )
        page.to_file()

        # Re-embed
        vec = generate_local_embedding(page.full_text)
        if vec:
            upsert_wiki_embedding(slug, spec.title, vec)

        # Extract first prose line for index summary
        summary_line = next(
            (ln.strip() for ln in new_body.splitlines()
             if ln.strip() and not ln.startswith("#")),
            spec.description,
        )
        updated_summaries.append({
            "slug":     slug,
            "title":    spec.title,
            "summary":  summary_line[:100],
            "category": spec.category,
            "updated":  datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        })
        result["synthesized"].append(slug)

    # ── Step 3: Update wiki/index.md ──────────────────────────────────────────
    if updated_summaries:
        _update_index(updated_summaries)

    # ── Step 4: Append to wiki/log.md ─────────────────────────────────────────
    _append_log(source_name, result["synthesized"])

    return result


# ═══════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════

def _load_all_schema_specs():
    from wiki import load_wiki_schema
    return load_wiki_schema()


def _route(schema_lines: list[str], source_name: str, source_text: str) -> list[str]:
    """Routing pass — returns list of relevant slugs."""
    if not CLAUDE_API_KEY:
        return []

    try:
        resp = _get_client().messages.create(
            model      = WIKI_ROUTING_MODEL,
            max_tokens = 256,
            system     = [{
                "type":          "text",
                "text":          _ROUTING_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }],
            messages = [{
                "role":    "user",
                "content": _routing_user(schema_lines, source_name, source_text),
            }],
        )
        raw   = resp.content[0].text.strip()
        match = re.search(r"\[.*?\]", raw, re.DOTALL)
        return json.loads(match.group()) if match else []
    except Exception:
        return []


def _synthesize(
    spec:          WikiPageSpec,
    existing_body: str | None,
    source_name:   str,
    source_text:   str,
    other_pages:   list[str],
) -> str:
    """Synthesis pass — returns new page body markdown."""
    if not CLAUDE_API_KEY:
        # Fallback: basic structured summary without LLM
        return (
            f"*Ingested from: {source_name}*\n\n"
            f"{source_text[:2000]}"
        )

    try:
        resp = _get_client().messages.create(
            model      = WIKI_SYNTHESIS_MODEL,
            max_tokens = 2048,
            system     = [{
                "type":          "text",
                "text":          _SYNTHESIS_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }],
            messages = [{
                "role":    "user",
                "content": _synthesis_user(
                    spec          = spec,
                    existing_body = existing_body,
                    source_name   = source_name,
                    source_text   = source_text,
                    other_pages   = other_pages,
                ),
            }],
        )
        return resp.content[0].text.strip()
    except Exception as exc:
        return f"*Synthesis failed: {exc}*\n\n*Source: {source_name}*"


def _update_index(updated_pages: list[dict]) -> None:
    from wiki import wiki_dir_ready
    wiki_dir = wiki_dir_ready()
    index_path = wiki_dir / "index.md"

    current = index_path.read_text(encoding="utf-8") if index_path.exists() else ""

    if not CLAUDE_API_KEY:
        # Fallback: append a simple table row
        if not current:
            current = "# Wiki Index\n\n| Page | Summary | Category | Updated |\n|---|---|---|---|\n"
        for p in updated_pages:
            current += f"| [[{p['slug']}\\|{p['title']}]] | {p['summary']} | {p['category']} | {p['updated']} |\n"
        index_path.write_text(current, encoding="utf-8")
        return

    try:
        resp = _get_client().messages.create(
            model      = WIKI_ROUTING_MODEL,   # Haiku is fine for index maintenance
            max_tokens = 1024,
            system     = [{
                "type":          "text",
                "text":          _INDEX_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }],
            messages = [{"role": "user", "content": _index_user(current, updated_pages)}],
        )
        index_path.write_text(resp.content[0].text.strip() + "\n", encoding="utf-8")
    except Exception:
        pass  # index update failure is non-fatal


def _append_log(source_name: str, updated_slugs: list[str]) -> None:
    from wiki import wiki_dir_ready
    log_path = wiki_dir_ready() / "log.md"
    ts    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    entry = f"\n## [{ts}] ingest | {source_name}\n- Updated: {', '.join(updated_slugs)}\n"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(entry)
