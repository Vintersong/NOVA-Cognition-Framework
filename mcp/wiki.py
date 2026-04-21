"""
wiki.py — WikiPage model, CRUD, and embedding index for NOVA's wiki layer.

Wiki pages are synthesized markdown files with YAML frontmatter, living in wiki/.
They are a parallel layer to shards with a different lifecycle:
  - Created/updated by nova_wiki_ingest from external sources only
  - Never auto-created from shard conversations
  - Don't decay — curated, evergreen, replaced when outdated
  - Can be referenced by procedural shards and skills
  - Can surface into shard context via nova_wiki_query

Wiki embedding index (wiki_index.json) mirrors shard_index.json but is keyed
by slug and stores only the data needed for cosine retrieval.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import WIKI_DIR, WIKI_INDEX_FILE


_WIKI_DIR   = Path(WIKI_DIR)
_INDEX_FILE = Path(WIKI_INDEX_FILE)

# Matches the --- frontmatter block at the start of a file
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
# Matches [[wikilinks]] in page bodies
_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


# ═══════════════════════════════════════════════════════════
# WIKI PAGE MODEL
# ═══════════════════════════════════════════════════════════

@dataclass
class WikiPage:
    slug: str
    title: str
    tags: list[str]
    updated: datetime
    sources: list[str]      # provenance: filenames/display names of ingested sources
    body: str               # markdown body — frontmatter excluded
    category: str = "general"

    @property
    def full_text(self) -> str:
        """Title + body — used as the embedding unit."""
        return f"# {self.title}\n\n{self.body}"

    @property
    def path(self) -> Path:
        return _WIKI_DIR / f"{self.slug}.md"

    @property
    def outbound_links(self) -> list[str]:
        """Slugs referenced via [[wikilink]] notation in body."""
        return _LINK_RE.findall(self.body)

    def to_file(self, path: Optional[Path] = None) -> None:
        target = path or self.path
        target.parent.mkdir(parents=True, exist_ok=True)

        tags_str    = ", ".join(self.tags)
        sources_str = "\n".join(f"  - {s}" for s in self.sources)

        fm = (
            f"---\n"
            f"title: {self.title}\n"
            f"slug: {self.slug}\n"
            f"category: {self.category}\n"
            f"tags: [{tags_str}]\n"
            f"updated: {self.updated.isoformat()}\n"
            f"sources:\n{sources_str}\n"
            f"---\n\n"
        )
        target.write_text(fm + self.body, encoding="utf-8")

    @classmethod
    def from_file(cls, path: Path) -> "WikiPage":
        text = path.read_text(encoding="utf-8")
        m    = _FM_RE.match(text)

        if not m:
            return cls(
                slug=path.stem, title=path.stem, tags=[],
                updated=datetime.now(timezone.utc), sources=[], body=text.strip(),
            )

        fm_text = m.group(1)
        body    = text[m.end():].strip()
        meta    = _parse_frontmatter(fm_text)

        updated_str = meta.get("updated", "")
        try:
            updated = datetime.fromisoformat(updated_str)
        except (ValueError, TypeError):
            updated = datetime.now(timezone.utc)

        return cls(
            slug     = meta.get("slug", path.stem),
            title    = meta.get("title", path.stem),
            category = meta.get("category", "general"),
            tags     = meta.get("tags", []),
            sources  = meta.get("sources", []),
            updated  = updated,
            body     = body,
        )


def _parse_frontmatter(fm_text: str) -> dict:
    """
    Parse the controlled YAML subset written by WikiPage.to_file().
    Handles: scalar strings, [inline, lists], multi-line list blocks.
    """
    meta: dict = {}
    lines  = fm_text.splitlines()
    i      = 0

    while i < len(lines):
        line = lines[i]

        # Skip blank lines
        if not line.strip():
            i += 1
            continue

        # Top-level key: value
        if ": " in line and not line.startswith(" "):
            key, _, val = line.partition(": ")
            key = key.strip()
            val = val.strip()

            # Inline list:  tags: [a, b, c]
            if val.startswith("[") and val.endswith("]"):
                inner = val[1:-1]
                meta[key] = [t.strip() for t in inner.split(",") if t.strip()]

            # Block list (next lines start with "  - ")
            elif val == "" and i + 1 < len(lines) and lines[i + 1].startswith("  - "):
                items: list[str] = []
                i += 1
                while i < len(lines) and lines[i].startswith("  - "):
                    items.append(lines[i][4:].strip())
                    i += 1
                meta[key] = items
                continue  # i already advanced

            else:
                meta[key] = val

        i += 1

    return meta


# ═══════════════════════════════════════════════════════════
# CRUD HELPERS
# ═══════════════════════════════════════════════════════════

def load_wiki_page(slug: str) -> Optional[WikiPage]:
    path = _WIKI_DIR / f"{slug}.md"
    if not path.exists():
        return None
    try:
        return WikiPage.from_file(path)
    except Exception:
        return None


def all_wiki_pages() -> list[WikiPage]:
    """Load every wiki page, skipping index.md and log.md."""
    _WIKI_DIR.mkdir(parents=True, exist_ok=True)
    pages = []
    for p in sorted(_WIKI_DIR.glob("*.md")):
        if p.stem in ("index", "log"):
            continue
        try:
            pages.append(WikiPage.from_file(p))
        except Exception:
            continue
    return pages


def wiki_dir_ready() -> Path:
    """Ensure the wiki directory exists and return its Path."""
    _WIKI_DIR.mkdir(parents=True, exist_ok=True)
    return _WIKI_DIR


# ═══════════════════════════════════════════════════════════
# EMBEDDING INDEX
# ═══════════════════════════════════════════════════════════

def load_wiki_index() -> dict:
    """
    Load wiki_index.json.
    Format: { slug: { "title": str, "embedding": list[float], "updated": str } }
    """
    if not _INDEX_FILE.exists():
        return {}
    try:
        with open(_INDEX_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_wiki_index(index: dict) -> None:
    with open(_INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f)     # no indent — embeddings make this huge


def upsert_wiki_embedding(slug: str, title: str, embedding: list[float]) -> None:
    index = load_wiki_index()
    index[slug] = {
        "title":     title,
        "embedding": embedding,
        "updated":   datetime.now(timezone.utc).isoformat(),
    }
    save_wiki_index(index)


def remove_wiki_embedding(slug: str) -> None:
    index = load_wiki_index()
    index.pop(slug, None)
    save_wiki_index(index)


# ═══════════════════════════════════════════════════════════
# WIKI SCHEMA
# ═══════════════════════════════════════════════════════════

@dataclass
class WikiPageSpec:
    """A single entry in the wiki schema — defines a topic page."""
    slug:        str
    title:       str
    description: str
    tags:        list[str] = field(default_factory=list)
    category:    str       = "general"

    def to_dict(self) -> dict:
        return {
            "slug":        self.slug,
            "title":       self.title,
            "description": self.description,
            "tags":        self.tags,
            "category":    self.category,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "WikiPageSpec":
        return cls(
            slug        = d["slug"],
            title       = d["title"],
            description = d.get("description", ""),
            tags        = d.get("tags", []),
            category    = d.get("category", "general"),
        )


def load_wiki_schema() -> list[WikiPageSpec]:
    from config import WIKI_SCHEMA_FILE
    schema_path = Path(WIKI_SCHEMA_FILE)
    if not schema_path.exists():
        return []
    try:
        with open(schema_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [WikiPageSpec.from_dict(p) for p in data.get("pages", [])]
    except Exception:
        return []


def save_wiki_schema(pages: list[WikiPageSpec]) -> None:
    from config import WIKI_SCHEMA_FILE
    schema_path = Path(WIKI_SCHEMA_FILE)
    with open(schema_path, "w", encoding="utf-8") as f:
        json.dump({"pages": [p.to_dict() for p in pages]}, f, indent=2)


def schema_by_slug(slug: str) -> Optional[WikiPageSpec]:
    return next((p for p in load_wiki_schema() if p.slug == slug), None)


def schema_summary_lines() -> list[str]:
    """One-line description per schema page — passed to routing prompt."""
    return [
        f"  {spec.slug}: {spec.title} — {spec.description}"
        for spec in load_wiki_schema()
    ]
