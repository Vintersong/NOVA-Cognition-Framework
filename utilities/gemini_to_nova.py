"""
gemini_to_nova.py — Migrate Gemini (Google Takeout "MyActivity") to NOVA shards

Google Takeout exports Gemini activity as MyActivity.html — a flat list of
~2000 standalone records, each an `outer-cell` containing a body-1
`content-cell` shaped roughly as:

    Prompted <user prompt><br><timestamp TZ><br><response HTML...>

The records are NOT threaded into conversations. We reconstruct "sessions" by
sorting all entries chronologically and starting a new session whenever the gap
between consecutive entries exceeds --gap-minutes (default 30). Each session
becomes one shard; each entry is one user→assistant turn.

Non-prompt records ("Created Gemini Canvas titled ...") are kept too, with the
action line as the user side.

Media/document files alongside MyActivity.html (jpg/png/mp4/pdf/...) are NOT
touched by this script.

Usage:
    python gemini_to_nova.py --input "../intake/gemini shards" --dry-run
    python gemini_to_nova.py --input "../intake/gemini shards" --gap-minutes 30
"""

import os
import re
import html
import json
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

DEFAULT_SHARD_DIR = Path(os.getenv(
    "NOVA_SHARD_DIR",
    Path(__file__).parent.parent / "shards"
))

BODY_RE = re.compile(
    r'content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">(.*?)</div>',
    re.DOTALL,
)
TS_RE = re.compile(
    r'([A-Z][a-z]{2} \d{1,2}, \d{4}, \d{1,2}:\d{2}:\d{2}\s*[AP]M)(?:\s+([A-Z]{2,5}))?'
)


# ═══════════════════════════════════════════════════════════
# HTML → TEXT
# ═══════════════════════════════════════════════════════════

def strip_html(fragment: str) -> str:
    """Convert an HTML fragment to readable plain text."""
    s = fragment
    s = re.sub(r'(?i)<\s*br\s*/?>', '\n', s)
    s = re.sub(r'(?i)</p\s*>', '\n\n', s)
    s = re.sub(r'(?i)<\s*li[^>]*>', '\n- ', s)
    s = re.sub(r'(?i)</(h[1-6]|div|ul|ol|tr)\s*>', '\n', s)
    s = re.sub(r'<[^>]+>', '', s)           # drop remaining tags
    s = html.unescape(s)
    s = s.replace(' ', ' ').replace('​', '')
    s = re.sub(r'[ \t]+\n', '\n', s)
    s = re.sub(r'\n{3,}', '\n\n', s)
    return s.strip()


def parse_entries(html_text: str) -> list[dict]:
    """Yield {ts: datetime, prompt: str, response: str} for each activity cell."""
    entries = []
    for body in BODY_RE.findall(html_text):
        m = TS_RE.search(body)
        if not m:
            continue
        ts_str = m.group(1).replace(' ', ' ')
        try:
            ts = datetime.strptime(ts_str, '%b %d, %Y, %I:%M:%S %p')
        except ValueError:
            continue

        before = body[:m.start()]
        after = body[m.end():]

        prompt = strip_html(before)
        # Strip the leading action verb ("Prompted " / "Created ... titled ")
        prompt = re.sub(r'^Prompted\s+', '', prompt).strip()
        response = strip_html(after)
        if not prompt and not response:
            continue
        entries.append({"ts": ts, "prompt": prompt, "response": response})
    return entries


def sessionize(entries: list[dict], gap_minutes: int) -> list[list[dict]]:
    entries = sorted(entries, key=lambda e: e["ts"])
    gap = timedelta(minutes=gap_minutes)
    sessions, cur = [], []
    for e in entries:
        if cur and (e["ts"] - cur[-1]["ts"]) > gap:
            sessions.append(cur)
            cur = []
        cur.append(e)
    if cur:
        sessions.append(cur)
    return sessions


# ═══════════════════════════════════════════════════════════
# SHARD CONSTRUCTION (mirrors chatgpt_to_nova.py)
# ═══════════════════════════════════════════════════════════

def sanitize_filename(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r'[^a-z0-9_]+', '_', name)
    name = re.sub(r'_+', '_', name)
    return name[:50].strip('_')


def infer_theme(title: str, turns: list[dict]) -> str:
    text = (title + " " + " ".join(
        (t.get("user", "") + " " + t.get("ai", "")) for t in turns[:3]
    )).lower()
    theme_keywords = {
        "game_design": ["game", "mechanic", "level", "player", "unity", "godot", "love2d", "lua", "sgdk"],
        "ai_ml": ["nova", "mcp", "llm", "gpt", "claude", "gemini", "grok", "embedding", "shard", "agent", "forgemaster", "cogniti", "ai"],
        "technical": ["python", "code", "function", "api", "server", "database", "bug", "error", "c++", "javascript"],
        "career": ["job", "resume", "cv", "interview", "apply", "linkedin", "internship", "salary"],
        "philosophy": ["meaning", "consciousness", "ethics", "philosophy", "theory", "exist", "grief"],
        "research": ["research", "paper", "study", "analysis", "data", "method", "literature", "thesis"],
        "personal": ["feel", "tired", "stressed", "life", "moved", "cluj", "sweden", "family"],
        "creative": ["story", "write", "design", "art", "music", "concept", "idea", "image", "canvas"],
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


def build_shard(session: list[dict], existing_ids: set) -> dict | None:
    turns = [{
        "timestamp": e["ts"].replace(tzinfo=timezone.utc).isoformat(),
        "user": e["prompt"],
        "ai": e["response"],
    } for e in session]
    if not turns:
        return None

    title = next((t["user"] for t in turns if t.get("user")), "") or \
        next((t["ai"][:80] for t in turns if t.get("ai")), "Gemini session")
    title = title[:80].replace("\n", " ").strip()

    theme = infer_theme(title, turns)
    intent = infer_intent(turns)
    day = session[0]["ts"].strftime("%Y%m%d")
    base_id = sanitize_filename(f"gemini_{theme}_{day}_{title[:24]}")
    shard_id = base_id
    n = 1
    while shard_id in existing_ids:
        shard_id = f"{base_id}_{n}"
        n += 1
    existing_ids.add(shard_id)

    created_at = turns[0]["timestamp"]
    return {
        "shard_id": shard_id,
        "guiding_question": f"What was discussed in: {title}?",
        "conversation_history": turns,
        "meta_tags": {
            "intent": intent,
            "theme": theme,
            "usage_count": 0,
            "last_used": created_at,
            "confidence": 1.0,
            "enrichment_status": "pending",
            "source": "gemini_export",
            "original_title": title,
            "original_file": "MyActivity.html",
            "session_start": created_at,
            "session_end": turns[-1]["timestamp"],
            "entry_count": len(turns),
            "imported_at": datetime.now(tz=timezone.utc).isoformat(),
        },
    }


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

def migrate(input_dir, output_dir, gap_minutes=30, min_turns=1, dry_run=False):
    in_path = Path(input_dir)
    out_path = Path(output_dir)
    print(f"\n{'[DRY RUN] ' if dry_run else ''}NOVA Migration: Gemini → Shards (gap={gap_minutes}min)")
    print(f"Input:  {in_path}")
    print(f"Output: {out_path}")
    print("─" * 50)

    activity = list(in_path.rglob("MyActivity.html"))
    if not activity:
        print("No MyActivity.html found.")
        return
    if not dry_run:
        out_path.mkdir(parents=True, exist_ok=True)

    existing_ids = set()
    created = skipped = 0
    theme_counts: dict[str, int] = {}

    for act in activity:
        print(f"Parsing {act.relative_to(in_path)} ...")
        entries = parse_entries(act.read_text(encoding="utf-8"))
        sessions = sessionize(entries, gap_minutes)
        print(f"  {len(entries)} entries → {len(sessions)} sessions")

        for sess in sessions:
            if len(sess) < min_turns:
                skipped += 1
                continue
            shard = build_shard(sess, existing_ids)
            if shard is None:
                skipped += 1
                continue
            theme = shard["meta_tags"]["theme"]
            theme_counts[theme] = theme_counts.get(theme, 0) + 1
            if dry_run:
                print(f"  [DRY] {shard['shard_id']}.json  ({len(sess)} turns, {theme})")
            else:
                with open(out_path / f"{shard['shard_id']}.json", "w", encoding="utf-8") as f:
                    json.dump(shard, f, indent=2, ensure_ascii=False)
                created += 1

    print("─" * 50)
    total = created if not dry_run else sum(theme_counts.values())
    print(f"{'[DRY] would create' if dry_run else '✅ created'}: {total} shards")
    print(f"⏭  skipped: {skipped}")
    print("\nTheme breakdown:")
    for theme, c in sorted(theme_counts.items(), key=lambda x: -x[1]):
        print(f"  {theme}: {c}")
    if not dry_run and created:
        print("\nNext: run nova_shard_consolidate to build the index.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Migrate Gemini MyActivity.html to NOVA shards")
    p.add_argument("--input", default="../intake/gemini shards")
    p.add_argument("--output", default=str(DEFAULT_SHARD_DIR))
    p.add_argument("--gap-minutes", type=int, default=30)
    p.add_argument("--min-turns", type=int, default=1)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    migrate(args.input, args.output, args.gap_minutes, args.min_turns, args.dry_run)
