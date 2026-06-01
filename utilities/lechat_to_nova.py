"""
lechat_to_nova.py — Migrate Le Chat (Mistral) exported conversations to NOVA shards

Each Le Chat export is a JSON file containing a flat list of message objects:
    {id, chatId, content, role: "user"|"assistant", createdAt, files: [...], ...}

IMPORTANT: messages are NOT stored in chronological order — they must be sorted
by `createdAt` to reconstruct the conversation. Attachment file names (the
`files` field, mirrored by the sibling `<chat-id>-files/` directories) are
folded into the message text so they remain searchable; the binary payloads
themselves are not ingested.

Output format mirrors utilities/chatgpt_to_nova.py / perplexity_to_nova.py so
shards are indistinguishable to the index/consolidator.

Usage:
    python lechat_to_nova.py --input "../intake/lechat shards" --dry-run
    python lechat_to_nova.py --input "../intake/lechat shards"
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


# ═══════════════════════════════════════════════════════════
# PARSING
# ═══════════════════════════════════════════════════════════

def message_text(msg: dict) -> str:
    """Message content plus a trailing note of any attachment names."""
    text = (msg.get("content") or "").strip()
    files = msg.get("files") or []
    names = [f.get("name", "") for f in files if isinstance(f, dict) and f.get("name")]
    if names:
        note = "[Attached: " + ", ".join(names) + "]"
        text = f"{text}\n\n{note}".strip()
    return text


def parse_chat(messages: list[dict]) -> list[dict]:
    """Sort messages chronologically and pair into user→assistant turns."""
    ordered = sorted(
        (m for m in messages if isinstance(m, dict) and m.get("role") in ("user", "assistant")),
        key=lambda m: m.get("createdAt", ""),
    )
    history = []
    for m in ordered:
        content = message_text(m)
        if not content:
            continue
        history.append({"role": m["role"], "content": content, "ts": m.get("createdAt", "")})

    turns = []
    i = 0
    while i < len(history):
        entry = history[i]
        if entry["role"] == "user":
            ts = entry["ts"]
            user = entry["content"]
            ai = ""
            if i + 1 < len(history) and history[i + 1]["role"] == "assistant":
                ai = history[i + 1]["content"]
                i += 2
            else:
                i += 1
            turns.append({"timestamp": ts, "user": user, "ai": ai})
        else:
            # Orphan assistant turn (e.g. conversation opens with an answer)
            turns.append({"timestamp": entry["ts"], "user": "", "ai": entry["content"]})
            i += 1
    return turns


def collect_attachments(messages: list[dict]) -> list[str]:
    names = []
    for m in messages:
        for f in (m.get("files") or []):
            if isinstance(f, dict) and f.get("name"):
                names.append(f["name"])
    return names


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
        "ai_ml": ["nova", "mcp", "llm", "gpt", "claude", "embedding", "shard", "agent", "forgemaster", "cogniti", "ai"],
        "technical": ["python", "code", "function", "api", "server", "database", "bug", "error", "c++", "javascript"],
        "career": ["job", "resume", "cv", "interview", "apply", "linkedin", "internship", "salary"],
        "philosophy": ["meaning", "consciousness", "ethics", "philosophy", "theory", "exist", "grief"],
        "research": ["research", "paper", "study", "analysis", "data", "method", "literature", "thesis"],
        "personal": ["feel", "tired", "stressed", "life", "moved", "cluj", "sweden", "family", "cynical"],
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
    for t in turns:
        if t.get("user"):
            return t["user"][:80].strip().replace("\n", " ")
    for t in turns:
        if t.get("ai"):
            return t["ai"][:80].strip().replace("\n", " ")
    return fallback


def build_shard(turns, title, created_at, existing_ids, chat_id, original_name, attachments):
    if not turns:
        return None
    theme = infer_theme(title, turns)
    intent = infer_intent(turns)
    base_id = sanitize_filename(f"lechat_{theme}_{title[:30]}")
    shard_id = base_id
    n = 1
    while shard_id in existing_ids:
        shard_id = f"{base_id}_{n}"
        n += 1
    existing_ids.add(shard_id)

    meta = {
        "intent": intent,
        "theme": theme,
        "usage_count": 0,
        "last_used": created_at,
        "confidence": 1.0,
        "enrichment_status": "pending",
        "source": "lechat_export",
        "original_title": title,
        "original_file": original_name,
        "original_id": chat_id,
        "imported_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    if attachments:
        meta["attachments"] = attachments

    return {
        "shard_id": shard_id,
        "guiding_question": f"What was discussed in: {title}?",
        "conversation_history": turns,
        "meta_tags": meta,
    }


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

def migrate(input_dir: str, output_dir: str, min_turns: int = 1, dry_run: bool = False):
    in_path = Path(input_dir)
    out_path = Path(output_dir)
    print(f"\n{'[DRY RUN] ' if dry_run else ''}NOVA Migration: Le Chat → Shards")
    print(f"Input:  {in_path}")
    print(f"Output: {out_path}")
    print("─" * 50)

    json_files = sorted(in_path.glob("chat-*.json"))
    print(f"Found {len(json_files)} chat file(s)")
    if not json_files:
        return
    if not dry_run:
        out_path.mkdir(parents=True, exist_ok=True)

    existing_ids = set()
    created = skipped_empty = skipped_short = errors = 0
    theme_counts: dict[str, int] = {}

    for fp in json_files:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            if not isinstance(data, list) or not data:
                skipped_empty += 1
                continue

            turns = parse_chat(data)
            if len(turns) < min_turns:
                skipped_short += 1
                continue

            chat_id = data[0].get("chatId", fp.stem)
            attachments = collect_attachments(data)
            title = title_from_turns(turns, fp.stem)
            created_at = turns[0]["timestamp"] or datetime.now(tz=timezone.utc).isoformat()

            shard = build_shard(turns, title, created_at, existing_ids, chat_id, fp.name, attachments)
            if shard is None:
                skipped_empty += 1
                continue

            theme = shard["meta_tags"]["theme"]
            theme_counts[theme] = theme_counts.get(theme, 0) + 1

            if dry_run:
                att = f" +{len(attachments)} files" if attachments else ""
                print(f"  [DRY] {shard['shard_id']}.json  ({len(turns)} turns, {theme}{att})")
            else:
                with open(out_path / f"{shard['shard_id']}.json", "w", encoding="utf-8") as f:
                    json.dump(shard, f, indent=2, ensure_ascii=False)
                created += 1
        except Exception as e:
            errors += 1
            print(f"  ✗ {fp.name}: {e}")

    print("─" * 50)
    total = len(json_files) - skipped_empty - skipped_short
    print(f"{'[DRY] would create' if dry_run else '✅ created'}: {created if not dry_run else total} shards")
    print(f"⏭  empty: {skipped_empty}   ⏭  too short (<{min_turns}): {skipped_short}   ❌ errors: {errors}")
    print("\nTheme breakdown:")
    for theme, c in sorted(theme_counts.items(), key=lambda x: -x[1]):
        print(f"  {theme}: {c}")
    if not dry_run and created:
        print("\nNext: run nova_shard_consolidate to build the index.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Migrate Le Chat export to NOVA shards")
    p.add_argument("--input", default="../intake/lechat shards")
    p.add_argument("--output", default=str(DEFAULT_SHARD_DIR))
    p.add_argument("--min-turns", type=int, default=1)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    migrate(args.input, args.output, args.min_turns, args.dry_run)
