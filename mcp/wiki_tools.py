"""
wiki_tools.py — MCP tool handlers for NOVA's wiki layer.

Registered into the FastMCP instance via register_wiki_tools(mcp).

Tools:
  nova_wiki_schema  — view or modify the topic taxonomy
  nova_wiki_ingest  — ingest a source document into the wiki
  nova_wiki_query   — semantic search over wiki pages
  nova_wiki_get     — read a specific wiki page in full
  nova_wiki_list    — list all pages with one-line summaries
  nova_wiki_lint    — health check: orphans, broken links, contradictions
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

from schemas import (
    WikiSchemaInput,
    WikiIngestInput,
    WikiQueryInput,
    WikiGetInput,
    WikiListInput,
    WikiLintInput,
)
from wiki import (
    WikiPageSpec,
    load_wiki_page,
    all_wiki_pages,
    load_wiki_schema,
    save_wiki_schema,
    schema_by_slug,
    load_wiki_index,
    wiki_dir_ready,
)
from wiki_ingest import ingest_source
from nova_embeddings_local import generate_local_embedding


# ═══════════════════════════════════════════════════════════
# REGISTRATION ENTRY POINT
# ═══════════════════════════════════════════════════════════

def register_wiki_tools(mcp) -> None:
    """Register all wiki MCP tools into the FastMCP instance."""

    # ── nova_wiki_schema ──────────────────────────────────────────────────────

    @mcp.tool(name="nova_wiki_schema")
    async def nova_wiki_schema(params: WikiSchemaInput) -> str:
        """
        View or modify the wiki topic taxonomy.

        action="get"    — return the full schema as JSON
        action="add"    — add a new page spec (slug, title, description, tags, category)
        action="remove" — remove a page spec by slug (does not delete the wiki file)
        """
        pages = load_wiki_schema()

        if params.action == "get":
            return json.dumps({
                "page_count": len(pages),
                "pages": [p.to_dict() for p in pages],
            }, indent=2)

        if params.action == "add":
            if not params.slug or not params.title:
                return json.dumps({"status": "error", "message": "slug and title are required."})
            if any(p.slug == params.slug for p in pages):
                return json.dumps({"status": "error", "message": f"Slug '{params.slug}' already exists."})
            tags = [t.strip() for t in params.tags.split(",") if t.strip()]
            new_spec = WikiPageSpec(
                slug        = params.slug,
                title       = params.title,
                description = params.description,
                tags        = tags,
                category    = params.category or "general",
            )
            pages.append(new_spec)
            save_wiki_schema(pages)
            return json.dumps({
                "status":   "added",
                "slug":     params.slug,
                "title":    params.title,
                "total":    len(pages),
            }, indent=2)

        if params.action == "remove":
            if not params.slug:
                return json.dumps({"status": "error", "message": "slug is required."})
            before = len(pages)
            pages  = [p for p in pages if p.slug != params.slug]
            if len(pages) == before:
                return json.dumps({"status": "error", "message": f"Slug '{params.slug}' not found."})
            save_wiki_schema(pages)
            return json.dumps({
                "status":  "removed",
                "slug":    params.slug,
                "total":   len(pages),
                "note":    "Wiki file (if any) was not deleted.",
            }, indent=2)

        return json.dumps({"status": "error", "message": f"Unknown action: {params.action}"})

    # ── nova_wiki_ingest ──────────────────────────────────────────────────────

    @mcp.tool(name="nova_wiki_ingest")
    async def nova_wiki_ingest(params: WikiIngestInput) -> str:
        """
        Ingest a source document into the wiki.

        source: local file path or raw text content
        source_name: display name used in provenance (auto-derived from path if blank)
        dry_run: route only, no files written

        Pipeline:
          1. Haiku routing pass — which schema slugs are relevant?
          2. Sonnet synthesis pass — write/update each page
          3. Re-embed updated pages
          4. Update wiki/index.md and wiki/log.md
        """
        source    = params.source
        path      = Path(source)

        # Resolve source text
        if path.exists() and path.is_file():
            source_text = path.read_text(encoding="utf-8", errors="replace")[:50_000]
            source_name = params.source_name or path.name
        else:
            # Treat as raw text
            source_text = source[:50_000]
            source_name = params.source_name or "inline_text"

        result = ingest_source(
            source_text = source_text,
            source_name = source_name,
            dry_run     = params.dry_run,
        )
        return json.dumps(result, indent=2)

    # ── nova_wiki_query ───────────────────────────────────────────────────────

    @mcp.tool(name="nova_wiki_query")
    async def nova_wiki_query(params: WikiQueryInput) -> str:
        """
        Semantic search over wiki pages using cosine similarity.

        Returns top-n pages with slug, title, score, and a short excerpt.
        Falls back to keyword overlap if no embeddings are available.
        """
        index = load_wiki_index()

        if not index:
            # Fallback: keyword search over page titles and bodies
            pages   = all_wiki_pages()
            query_l = params.query.lower()
            scored  = []
            for p in pages:
                hits = p.full_text.lower().count(query_l)
                if hits:
                    scored.append((p, hits))
            scored.sort(key=lambda x: x[1], reverse=True)
            results = [
                {
                    "slug":    p.slug,
                    "title":   p.title,
                    "score":   hits,
                    "excerpt": _excerpt(p.body, params.query),
                    "method":  "keyword",
                }
                for p, hits in scored[: params.top_n]
            ]
            return json.dumps({"query": params.query, "results": results}, indent=2)

        # Embed the query
        query_vec = generate_local_embedding(params.query)
        if not query_vec:
            return json.dumps({"status": "error", "message": "Embedding model unavailable."})

        # Score every indexed page
        scored = []
        for slug, entry in index.items():
            page_vec = entry.get("embedding")
            if not page_vec:
                continue
            sim = _cosine(query_vec, page_vec)
            if sim > 0.0:
                scored.append((slug, entry.get("title", slug), sim))

        scored.sort(key=lambda x: x[2], reverse=True)

        results = []
        for slug, title, score in scored[: params.top_n]:
            page = load_wiki_page(slug)
            results.append({
                "slug":    slug,
                "title":   title,
                "score":   round(score, 4),
                "excerpt": _excerpt(page.body if page else "", params.query),
                "method":  "cosine",
            })

        return json.dumps({"query": params.query, "results": results}, indent=2)

    # ── nova_wiki_get ─────────────────────────────────────────────────────────

    @mcp.tool(name="nova_wiki_get")
    async def nova_wiki_get(params: WikiGetInput) -> str:
        """Read a specific wiki page in full."""
        page = load_wiki_page(params.slug)
        if page is None:
            return json.dumps({
                "status":  "error",
                "message": f"No wiki page found for slug '{params.slug}'.",
            }, indent=2)
        return json.dumps({
            "slug":     page.slug,
            "title":    page.title,
            "category": page.category,
            "tags":     page.tags,
            "updated":  page.updated.isoformat(),
            "sources":  page.sources,
            "links":    page.outbound_links,
            "body":     page.body,
        }, indent=2)

    # ── nova_wiki_list ────────────────────────────────────────────────────────

    @mcp.tool(name="nova_wiki_list")
    async def nova_wiki_list(params: WikiListInput) -> str:
        """
        List all wiki pages with one-line summaries.
        Optionally filter by category.
        """
        pages = all_wiki_pages()

        if params.category:
            pages = [p for p in pages if p.category == params.category]

        rows = []
        for p in sorted(pages, key=lambda x: x.title):
            first_line = next(
                (ln.strip() for ln in p.body.splitlines()
                 if ln.strip() and not ln.startswith("#")),
                "",
            )
            rows.append({
                "slug":     p.slug,
                "title":    p.title,
                "category": p.category,
                "tags":     p.tags,
                "updated":  p.updated.strftime("%Y-%m-%d"),
                "sources":  len(p.sources),
                "summary":  first_line[:120],
            })

        return json.dumps({
            "total":    len(rows),
            "category": params.category or "all",
            "pages":    rows,
        }, indent=2)

    # ── nova_wiki_lint ────────────────────────────────────────────────────────

    @mcp.tool(name="nova_wiki_lint")
    async def nova_wiki_lint(params: WikiLintInput) -> str:
        """
        Health check the wiki.

        Checks:
          - Orphan pages: no inbound [[wikilinks]] from other pages
          - Broken links: [[slug]] references pointing to non-existent pages
          - Missing embeddings: pages not yet in wiki_index.json
          - Stale pages: not updated in 30+ days

        deep=True: also runs an LLM contradiction check across all pages
          (expensive — uses Sonnet, one pass per page pair)
        """
        pages    = all_wiki_pages()
        index    = load_wiki_index()
        all_slugs = {p.slug for p in pages}

        # Build inbound link map
        inbound: dict[str, list[str]] = {p.slug: [] for p in pages}
        for p in pages:
            for link in p.outbound_links:
                if link in inbound:
                    inbound[link].append(p.slug)

        orphans        = [s for s, refs in inbound.items() if not refs]
        broken_links   = []
        missing_embeds = []
        stale          = []
        now            = datetime.now(timezone.utc)

        for p in pages:
            for link in p.outbound_links:
                if link not in all_slugs:
                    broken_links.append({"page": p.slug, "broken_link": link})

            if p.slug not in index:
                missing_embeds.append(p.slug)

            days_old = (now - p.updated.replace(tzinfo=timezone.utc)
                        if p.updated.tzinfo is None
                        else now - p.updated).days
            if days_old > 30:
                stale.append({"slug": p.slug, "days_since_update": days_old})

        report: dict = {
            "total_pages":       len(pages),
            "orphan_pages":      orphans,
            "broken_links":      broken_links,
            "missing_embeddings": missing_embeds,
            "stale_pages":       stale,
            "deep_lint":         None,
        }

        if params.deep and pages:
            report["deep_lint"] = _deep_lint(pages)

        return json.dumps(report, indent=2)


# ═══════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════

def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot    = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _excerpt(body: str, query: str, window: int = 200) -> str:
    """Return a short excerpt around the first occurrence of the query."""
    if not body:
        return ""
    lower = body.lower()
    pos   = lower.find(query.lower())
    if pos == -1:
        return body[:window].replace("\n", " ").strip()
    start = max(0, pos - 80)
    end   = min(len(body), pos + window)
    return ("..." if start > 0 else "") + body[start:end].replace("\n", " ").strip()


def _deep_lint(pages) -> dict:
    """
    LLM-based contradiction check. Compares each page against a summary
    of all others, looking for factual conflicts.
    Returns {slug: [list of potential contradictions]} for pages with issues.
    """
    from config import CLAUDE_API_KEY, WIKI_SYNTHESIS_MODEL
    import anthropic

    if not CLAUDE_API_KEY:
        return {"error": "CLAUDE_API_KEY not set — deep lint unavailable."}

    # Build a compact summary of all pages (title + first 300 chars of body)
    summaries = "\n\n".join(
        f"### {p.title} ({p.slug})\n{p.body[:300]}"
        for p in pages
    )

    contradictions: dict[str, list[str]] = {}

    try:
        client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
        resp   = client.messages.create(
            model      = WIKI_SYNTHESIS_MODEL,
            max_tokens = 1024,
            system     = [{
                "type":          "text",
                "text":          (
                    "You are a wiki health checker. Given summaries of wiki pages, "
                    "identify any factual contradictions between pages. "
                    "Return ONLY a JSON object: "
                    "{\"slug\": [\"contradiction description\", ...]}. "
                    "If no contradictions are found, return {}."
                ),
                "cache_control": {"type": "ephemeral"},
            }],
            messages = [{"role": "user", "content": summaries}],
        )
        raw = resp.content[0].text.strip()
        # Extract JSON object
        import re
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            contradictions = json.loads(m.group())
    except Exception as exc:
        return {"error": str(exc)}

    return contradictions
