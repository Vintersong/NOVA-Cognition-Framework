"""
perplexity_to_nova.py — Migrate Perplexity exported threads to NOVA shards

Perplexity exports each conversation as a Markdown file where the user's
prompts are rendered as level-1 headings (`# ...`) and the assistant's
research answer follows until the next `# ...` heading. The first block is
the Perplexity logo banner and is discarded.

`perplexity-memories.md` is not a conversation — it is a flat, dated list of
remembered facts (date / category / fact triples). It is special-cased into a
single user-profile shard.

Output format mirrors utilities/chatgpt_to_nova.py exactly so the resulting
shards are indistinguishable from other imports to the index/consolidator.

Usage:
    python perplexity_to_nova.py --input "../intake/perplexity shards" --dry-run
    python perplexity_to_nova.py --input "../intake/perplexity shards"
"""

import os
import re
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_SHARD_DIR = Path(os.getenv(
    "NOVA_SHARD_DIR",
    Path(__file__).parent.parent / "shards"
))

MEMORIES_FILENAME = "perplexity-memories.md"


# ═══════════════════════════════════════════════════════════
# CLEANING
# ═══════════════════════════════════════════════════════════

def clean_block(text: str) -> str:
    """Strip Perplexity decoration that adds no semantic value."""
    # Hidden citation-number spans: <span style="display:none">[^x]...</span>
    text = re.sub(r'<span style="display:none">.*?</span>', '', text, flags=re.DOTALL)
    # Decorative end-of-answer divider
    text = re.sub(r'<div align="center">⁂</div>', '', text)
    # Logo banner img tag
    text = re.sub(r'<img src="https://r2cdn\.perplexity\.ai/.*?/>', '', text, flags=re.DOTALL)
    return text.strip()


# ═══════════════════════════════════════════════════════════
# CONVERSATION PARSING
# ═══════════════════════════════════════════════════════════

def parse_thread(md: str) -> list[dict]:
    """
    Split a Perplexity markdown thread into [{user, ai}, ...] turns.

    User prompts are level-1 headings (`# ` at line start). Everything from a
    heading up to the next heading is that turn's assistant answer. The text
    before the first heading is the logo banner / preamble and is dropped.
    """
    # Split keeping the heading text; (?m) so ^ matches each line start.
    parts = re.split(r'(?m)^#[ \t]+(.+?)[ \t]*$', md)
    # parts[0] = preamble; then alternating (heading, body) pairs.
    turns = []
    for i in range(1, len(parts), 2):
        user = clean_block(parts[i])
        ai = clean_block(parts[i + 1]) if i + 1 < len(parts) else ""
        if not user and not ai:
            continue
        turns.append({"user": user, "ai": ai})

    if turns:
        return turns

    # Fallback: no H1 user-turn headings (e.g. random.md) — preserve the whole
    # document as one turn. The first non-empty line seeds the guiding question.
    body = clean_block(md)
    if not body:
        return []
    first_line = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
    return [{"user": first_line[:120], "ai": body}]


def parse_memories(md: str) -> list[dict]:
    """
    Parse perplexity-memories.md (date / category / fact triples) into a single
    assistant turn grouping facts by category.
    """
    lines = [ln.strip() for ln in md.splitlines() if ln.strip()]
    date_re = re.compile(r'^[A-Z][a-z]{2} \d{1,2}, \d{4}$')
    by_category: dict[str, list[str]] = {}
    i = 0
    while i + 2 < len(lines) + 1:
        if i + 2 < len(lines) and date_re.match(lines[i]):
            date, category, fact = lines[i], lines[i + 1], lines[i + 2]
            by_category.setdefault(category, []).append(f"({date}) {fact}")
            i += 3
        else:
            i += 1
    sections = []
    for category in sorted(by_category):
        body = "\n".join(f"- {f}" for f in by_category[category])
        sections.append(f"### {category}\n{body}")
    return [{
        "user": "What does the Perplexity assistant remember about the user?",
        "ai": "\n\n".join(sections),
    }]


# ═══════════════════════════════════════════════════════════
# SHARD CONSTRUCTION (mirrors chatgpt_to_nova.py)
# ═══════════════════════════════════════════════════════════

def sanitize_filename(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r'[^a-z0-9_]+', '_', name)
    name = re.sub(r'_+', '_', name)
    return name[:50].strip('_')


def infer_theme(title: str, turns: list[dict]) -> str:
    # Consider both prompts and answer text so headingless dumps (where the
    # topic lives in the answer body) classify correctly.
    text = (title + " " + " ".join(
        (t.get("user", "") + " " + t.get("ai", "")) for t in turns[:3]
    )).lower()
    theme_keywords = {
        "game_design": ["game", "mechanic", "level", "player", "unity", "godot", "love2d", "lua", "sgdk"],
        "ai_ml": ["nova", "mcp", "llm", "gpt", "claude", "embedding", "shard", "agent", "forgemaster", "cogniti", "ai"],
        "technical": ["python", "code", "function", "api", "server", "database", "bug", "error", "c++", "javascript"],
        "career": ["job", "resume", "cv", "interview", "apply", "linkedin", "internship", "salary"],
        "philosophy": ["meaning", "consciousness", "ethics", "philosophy", "theory", "exist", "grief"],
        "research": ["research", "paper", "study", "analysis", "data", "method", "literature", "thesis"],
        "personal": ["feel", "tired", "stressed", "life", "moved", "cluj", "sweden", "family", "memor"],
        "creative": ["story", "write", "design", "art", "music", "concept", "idea"],
    }
    scores = {t: sum(1 for kw in kws if kw in text) for t, kws in theme_keywords.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "general"


def infer_intent(turns: list[dict]) -> str:
    if not turns:
        return "reflection"
    first = turns[0].get("user", "").lower()
    if any(w in first for w in ["help", "how", "what", "why", "explain", "find", "search", "??"]):
        return "research"
    if any(w in first for w in ["build", "create", "make", "write", "design"]):
        return "planning"
    if any(w in first for w in ["fix", "error", "bug", "wrong", "broken"]):
        return "brainstorm"
    return "reflection"


def title_from_turns(turns: list[dict], fallback: str) -> str:
    if turns and turns[0].get("user"):
        return turns[0]["user"][:80].strip()
    return fallback


def build_shard(turns: list[dict], title: str, created_at: str,
                existing_ids: set, source: str, original_name: str) -> dict | None:
    if not turns:
        return None
    theme = infer_theme(title, turns)
    intent = infer_intent(turns)
    base_id = sanitize_filename(f"perplexity_{theme}_{title[:30]}")
    shard_id = base_id
    n = 1
    while shard_id in existing_ids:
        shard_id = f"{base_id}_{n}"
        n += 1
    existing_ids.add(shard_id)

    history = [{
        "timestamp": created_at,
        "user": t.get("user", ""),
        "ai": t.get("ai", ""),
    } for t in turns]

    return {
        "shard_id": shard_id,
        "guiding_question": f"What was discussed in: {title}?",
        "conversation_history": history,
        "meta_tags": {
            "intent": intent,
            "theme": theme,
            "usage_count": 0,
            "last_used": created_at,
            "confidence": 1.0,
            "enrichment_status": "pending",
            "source": source,
            "original_title": title,
            "original_file": original_name,
            "imported_at": datetime.now(tz=timezone.utc).isoformat(),
        },
    }


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

def migrate(input_dir: str, output_dir: str, min_turns: int = 1, dry_run: bool = False):
    in_path = Path(input_dir)
    out_path = Path(output_dir)
    print(f"\n{'[DRY RUN] ' if dry_run else ''}NOVA Migration: Perplexity → Shards")
    print(f"Input:  {in_path}")
    print(f"Output: {out_path}")
    print("─" * 50)

    md_files = sorted(in_path.glob("*.md"))
    print(f"Found {len(md_files)} markdown file(s)")
    if not md_files:
        return
    if not dry_run:
        out_path.mkdir(parents=True, exist_ok=True)

    existing_ids = set()
    created = skipped = errors = 0
    theme_counts: dict[str, int] = {}

    for fp in md_files:
        try:
            md = fp.read_text(encoding="utf-8")
            mtime = datetime.fromtimestamp(fp.stat().st_mtime, tz=timezone.utc).isoformat()

            if fp.name == MEMORIES_FILENAME:
                turns = parse_memories(md)
                title = "Perplexity remembered facts about the user"
                source = "perplexity_memories"
            else:
                turns = parse_thread(md)
                title = title_from_turns(turns, fp.stem)
                source = "perplexity_export"

            if len(turns) < min_turns:
                skipped += 1
                print(f"  ⏭  {fp.name}: {len(turns)} turn(s) < min")
                continue

            shard = build_shard(turns, title, mtime, existing_ids, source, fp.name)
            if shard is None:
                skipped += 1
                continue

            theme = shard["meta_tags"]["theme"]
            theme_counts[theme] = theme_counts.get(theme, 0) + 1

            if dry_run:
                print(f"  [DRY] {shard['shard_id']}.json  ({len(turns)} turns, {theme})")
            else:
                with open(out_path / f"{shard['shard_id']}.json", "w", encoding="utf-8") as f:
                    json.dump(shard, f, indent=2, ensure_ascii=False)
                created += 1
        except Exception as e:
            errors += 1
            print(f"  ✗ {fp.name}: {e}")

    print("─" * 50)
    print(f"{'[DRY] would create' if dry_run else '✅ created'}: {created if not dry_run else len(md_files) - skipped} shards")
    print(f"⏭  skipped: {skipped}   ❌ errors: {errors}")
    print("\nTheme breakdown:")
    for theme, c in sorted(theme_counts.items(), key=lambda x: -x[1]):
        print(f"  {theme}: {c}")
    if not dry_run and created:
        print("\nNext: run nova_shard_consolidate to build the index.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Migrate Perplexity exports to NOVA shards")
    p.add_argument("--input", default="../intake/perplexity shards")
    p.add_argument("--output", default=str(DEFAULT_SHARD_DIR))
    p.add_argument("--min-turns", type=int, default=1)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    migrate(args.input, args.output, args.min_turns, args.dry_run)
