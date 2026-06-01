"""
docs_to_nova.py — Ingest curated intake DOCUMENTS into NOVA as reference shards

Unlike the conversation importers, this turns standalone documents (design docs,
GDDs, papers) into single-turn reference shards. It pulls from three places:

  intake/gemini shards/Gemini Apps/   — loose docs (extensionless md, txt, json,
                                        pdf, docx, pptx); gemini appends a
                                        -<hash> suffix to file names
  intake/claude shards/projects/*.json — Claude Projects whose `docs[]` already
                                        carry extracted plain-text `content`
  intake/<root>/*.md                   — loose NOVA design docs

Only categories A (NOVA), B (game design), C (academic) are ingested. Code
snapshots and personal/admin files are skipped. Duplicates (by extracted-text
hash) collapse to one shard.

Text extraction: plain read for text; PyMuPDF (fitz) for PDF; manual unzip for
docx/pptx. Files whose text can't be extracted are reported and skipped.

Usage:
    python docs_to_nova.py --dry-run
    python docs_to_nova.py
"""

import os
import re
import json
import glob
import html
import zipfile
import hashlib
import argparse
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).parent.parent
INTAKE = REPO / "intake"
DEFAULT_SHARD_DIR = Path(os.getenv("NOVA_SHARD_DIR", REPO / "shards"))

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None


# ═══════════════════════════════════════════════════════════
# CATEGORISATION  (A=nova, B=game, C=academic; else skip)
# ═══════════════════════════════════════════════════════════

A_NOVA = ["nova", "forgemaster", "invention_disclosure", "principle_library",
          "heavyskill", "skill_verification", "nova_overview", "cheatsheet",
          "ingestion_pipeline", "proof_of_concept", "similarity_analysis",
          "technical_feasibility", "skill_generator", "skill", "changes_and_roadmap"]
B_GAME = ["chromatic", "atom_defense", "neon rider", "neonrider", "labyrinth",
          "love2d", "gdd", "design_doc", "design document", "pitch_deck", "pitch deck",
          "pitch_deck", "tcpn", "crypto_design", "game_ideas", "evolving art",
          "design_specification", "executive summary", "completion_report",
          "phase_change", "nanobot", "game design document", "souls", "design_spec"]
C_ACAD = ["filosofie", "planetary_history", "philosophy_reflection",
          "technical_verification", "warcraft", "convergence culture", "paper idea",
          "research paper", "introduction", "sources and references", "digital media"]
# Explicit skips even if they brush a keyword
SKIP = ["resume", "_cv_", "cv_general", "master_resume", "intyg", "contract",
        "gmail", "schedule", "sep2025", "submission", "colours", "share pages",
        "random-thought", "readme", "requirements", "coera", "ivptrofee",
        "how to use claude", "claude prompting", "cat redistribution"]


def categorize(name: str) -> str | None:
    n = name.lower()
    if any(s in n for s in SKIP):
        return None
    if n.endswith(".py") or n.endswith(".xls") or n.endswith(".html"):
        return None
    if any(k in n for k in A_NOVA):
        return "nova_doc"
    if any(k in n for k in C_ACAD):
        return "academic_doc"
    if any(k in n for k in B_GAME):
        return "game_doc"
    return None


# ═══════════════════════════════════════════════════════════
# TEXT EXTRACTION
# ═══════════════════════════════════════════════════════════

def strip_xml(xml: str, para_tag: str) -> str:
    xml = re.sub(para_tag, "\n", xml)
    xml = re.sub(r"<[^>]+>", "", xml)
    return html.unescape(xml)


def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            if not fitz:
                return ""
            doc = fitz.open(path)
            return "\n".join(page.get_text() for page in doc).strip()
        if ext == ".docx":
            with zipfile.ZipFile(path) as z:
                return strip_xml(z.read("word/document.xml").decode("utf-8", "ignore"),
                                 r"</w:p>").strip()
        if ext == ".pptx":
            out = []
            with zipfile.ZipFile(path) as z:
                for n in sorted(x for x in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml", x)):
                    out.append(strip_xml(z.read(n).decode("utf-8", "ignore"), r"</a:p>"))
            return "\n".join(out).strip()
        # plain text (extensionless, .md, .txt, .json)
        return path.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception as e:
        print(f"    ! extract failed {path.name}: {e}")
        return ""


def clean_name(name: str) -> str:
    name = re.sub(r"-[0-9a-f]{12,}(?=\.|$)", "", name)   # gemini -<hash> suffix
    name = re.sub(r"\.(pdf|docx|pptx|txt|json|md|html)$", "", name, flags=re.I)
    name = re.sub(r"\s*-?\s*Copy$", "", name, flags=re.I)
    name = name.replace("_", " ").strip()
    return name or "Untitled document"


# ═══════════════════════════════════════════════════════════
# SHARD
# ═══════════════════════════════════════════════════════════

def sanitize_filename(name: str) -> str:
    name = re.sub(r"[^a-z0-9_]+", "_", name.lower().strip())
    return re.sub(r"_+", "_", name)[:50].strip("_")


def infer_theme(category: str, text: str) -> str:
    if category == "nova_doc":
        return "ai_ml"
    if category == "game_doc":
        return "game_design"
    return "research"


def make_doc_shard(title, text, category, existing_ids, origin):
    title = title[:80].replace("\n", " ").strip()
    theme = infer_theme(category, text)
    prefix = {"nova_doc": "doc_nova", "game_doc": "doc_game", "academic_doc": "doc_acad"}[category]
    base_id = sanitize_filename(f"{prefix}_{title[:34]}")
    shard_id = base_id
    n = 1
    while shard_id in existing_ids:
        shard_id = f"{base_id}_{n}"
        n += 1
    existing_ids.add(shard_id)
    now = datetime.now(tz=timezone.utc).isoformat()
    return {
        "shard_id": shard_id,
        "guiding_question": f"What does the reference document '{title}' contain?",
        "conversation_history": [{
            "timestamp": now,
            "user": f"Reference document: {title}",
            "ai": text,
        }],
        "meta_tags": {
            "intent": "reflection",
            "theme": theme,
            "usage_count": 0,
            "last_used": now,
            "confidence": 1.0,
            "enrichment_status": "pending",
            "source": category,
            "original_title": title,
            "origin": origin,
            "char_count": len(text),
            "imported_at": now,
        },
    }


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

def gather_candidates():
    """Yield (title, text, category, origin) for every A/B/C document."""
    # 1) gemini loose docs
    gdir = INTAKE / "gemini shards" / "Gemini Apps"
    for f in sorted(gdir.glob("*")):
        if not f.is_file() or f.name == "MyActivity.html":
            continue
        cat = categorize(f.name)
        if not cat:
            continue
        text = extract_text(f)
        if text:
            yield clean_name(f.name), text, cat, f"gemini/{f.name}"

    # 2) claude projects' embedded docs
    for pf in sorted((INTAKE / "claude shards" / "projects").glob("*.json")):
        proj = json.loads(pf.read_text(encoding="utf-8"))
        pname = proj.get("name", "")
        for doc in (proj.get("docs") or []):
            fn = doc.get("filename", "")
            cat = categorize(f"{pname} {fn}")
            content = (doc.get("content") or "").strip()
            if cat and content:
                yield clean_name(fn or pname), content, cat, f"claude_project/{pname}/{fn}"

    # 3) root .md design docs
    for f in sorted(INTAKE.glob("*.md")):
        cat = categorize(f.name)
        if not cat:
            continue
        text = extract_text(f)
        if text:
            yield clean_name(f.name), text, cat, f"root/{f.name}"


def migrate(output_dir, dry_run=False):
    out_path = Path(output_dir)
    print(f"\n{'[DRY RUN] ' if dry_run else ''}NOVA Migration: Documents → reference shards")
    print(f"PyMuPDF (PDF): {'yes' if fitz else 'NO — PDFs will be skipped'}")
    print("─" * 50)
    if not dry_run:
        out_path.mkdir(parents=True, exist_ok=True)

    existing_ids = set()
    seen_hashes = {}
    created = dupes = 0
    cat_counts: dict[str, int] = {}

    for title, text, cat, origin in gather_candidates():
        h = hashlib.md5(text.encode("utf-8", "ignore")).hexdigest()
        if h in seen_hashes:
            dupes += 1
            print(f"  ~ dup ({origin}) == {seen_hashes[h]}")
            continue
        seen_hashes[h] = title
        shard = make_doc_shard(title, text, cat, existing_ids, origin)
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
        if dry_run:
            print(f"  [DRY] {shard['shard_id']}.json  [{cat}] {len(text):>7}c  <- {origin}")
        else:
            with open(out_path / f"{shard['shard_id']}.json", "w", encoding="utf-8") as fh:
                json.dump(shard, fh, indent=2, ensure_ascii=False)
            created += 1

    print("─" * 50)
    total = created if not dry_run else sum(cat_counts.values())
    print(f"{'[DRY] would create' if dry_run else '✅ created'}: {total} reference shards   (deduped {dupes})")
    for c, n in sorted(cat_counts.items()):
        print(f"  {c}: {n}")
    if not dry_run and created:
        print("\nNext: run nova_shard_consolidate to build the index.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Ingest curated intake documents as NOVA reference shards")
    p.add_argument("--output", default=str(DEFAULT_SHARD_DIR))
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    migrate(args.output, args.dry_run)
