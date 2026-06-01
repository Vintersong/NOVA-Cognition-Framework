"""
claude_to_nova.py — Migrate Claude (Anthropic) data export to NOVA shards

Handles three parts of an Anthropic account export:

  conversations.json  — list of {uuid, name, summary, created_at, chat_messages:
                         [{sender: human|assistant, text, content:[blocks],
                           created_at, attachments, files}]}
  design_chats/*.json — {title, messages: [{role: user|assistant, content,
                         created_at}]}   (separate from conversations.json)
  memories.json       — [{conversations_memory: "<big profile blob>"}]
                         → one user-profile shard

`projects/*.json` (Claude Projects + their knowledge docs) are NOT ingested
here — they are documents, handled in the cross-export document triage.

Message text is taken from `text` when present, else assembled from `content`
text blocks (content may also be a plain string in design_chats). Messages are
sorted by created_at before pairing into user→assistant turns.

Usage:
    python claude_to_nova.py --input "../intake/claude shards" --dry-run
    python claude_to_nova.py --input "../intake/claude shards"
"""

import os
import re
import json
import glob
import argparse
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_SHARD_DIR = Path(os.getenv(
    "NOVA_SHARD_DIR",
    Path(__file__).parent.parent / "shards"
))


# ═══════════════════════════════════════════════════════════
# MESSAGE EXTRACTION
# ═══════════════════════════════════════════════════════════

def block_text(content) -> str:
    """Extract text from a `content` value: string, or list of typed blocks."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                parts.append(b["text"])
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(parts).strip()
    return ""


def message_text(m: dict) -> str:
    t = (m.get("text") or "").strip()
    if not t:
        t = block_text(m.get("content"))
    names = []
    for coll in ("attachments", "files"):
        for f in (m.get(coll) or []):
            if isinstance(f, dict):
                n = f.get("file_name") or f.get("name") or f.get("title")
                if n:
                    names.append(n)
    if names:
        t = f"{t}\n\n[Attached: {', '.join(names)}]".strip()
    return t


def normalize_role(m: dict) -> str:
    r = (m.get("sender") or m.get("role") or "").lower()
    return "user" if r in ("human", "user") else "assistant"


def messages_to_turns(messages: list[dict]) -> tuple[list[dict], int]:
    """Sort by created_at, pair into user→assistant turns. Returns (turns, attach_count)."""
    rows = []
    attach = 0
    for m in messages:
        attach += len(m.get("attachments") or []) + len(m.get("files") or [])
        txt = message_text(m)
        if not txt:
            continue
        rows.append({"role": normalize_role(m), "content": txt, "ts": m.get("created_at", "")})
    rows.sort(key=lambda r: r["ts"])

    turns = []
    i = 0
    while i < len(rows):
        r = rows[i]
        if r["role"] == "user":
            ai = ""
            ts = r["ts"]
            if i + 1 < len(rows) and rows[i + 1]["role"] == "assistant":
                ai = rows[i + 1]["content"]
                i += 2
            else:
                i += 1
            turns.append({"timestamp": ts, "user": r["content"], "ai": ai})
        else:
            turns.append({"timestamp": r["ts"], "user": "", "ai": r["content"]})
            i += 1
    return turns, attach


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
    if any(w in first for w in ["build", "create", "make", "write", "design", "implement"]):
        return "planning"
    if any(w in first for w in ["fix", "error", "bug", "wrong", "broken"]):
        return "brainstorm"
    return "reflection"


def make_shard(turns, title, created_at, existing_ids, source, orig_id, prefix, attach=0):
    if not turns:
        return None
    clean_title = (title or "").replace("\n", " ").strip() or "Untitled"
    theme = infer_theme(clean_title, turns)
    intent = infer_intent(turns)
    base_id = sanitize_filename(f"{prefix}_{theme}_{clean_title[:30]}")
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
        "last_used": created_at or datetime.now(tz=timezone.utc).isoformat(),
        "confidence": 1.0,
        "enrichment_status": "pending",
        "source": source,
        "original_title": clean_title,
        "original_id": orig_id,
        "imported_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    if attach:
        meta["attachment_count"] = attach

    return {
        "shard_id": shard_id,
        "guiding_question": f"What was discussed in: {clean_title}?",
        "conversation_history": turns,
        "meta_tags": meta,
    }


def title_from_turns(turns, fallback):
    for t in turns:
        if t.get("user"):
            return t["user"][:80]
    for t in turns:
        if t.get("ai"):
            return t["ai"][:80]
    return fallback


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

def migrate(input_dir, output_dir, min_turns=1, dry_run=False):
    in_path = Path(input_dir)
    out_path = Path(output_dir)
    print(f"\n{'[DRY RUN] ' if dry_run else ''}NOVA Migration: Claude → Shards")
    print(f"Input:  {in_path}")
    print(f"Output: {out_path}")
    print("─" * 50)
    if not dry_run:
        out_path.mkdir(parents=True, exist_ok=True)

    existing_ids = set()
    created = skipped = errors = 0
    theme_counts: dict[str, int] = {}

    def emit(shard):
        nonlocal created
        if dry_run:
            return
        with open(out_path / f"{shard['shard_id']}.json", "w", encoding="utf-8") as f:
            json.dump(shard, f, indent=2, ensure_ascii=False)
        created += 1

    # 1) conversations.json
    conv_path = in_path / "conversations.json"
    if conv_path.exists():
        print("Loading conversations.json (large) ...")
        convs = json.loads(conv_path.read_text(encoding="utf-8"))
        print(f"  {len(convs)} conversations")
        for c in convs:
            try:
                turns, attach = messages_to_turns(c.get("chat_messages", []))
                if len(turns) < min_turns:
                    skipped += 1
                    continue
                title = c.get("name") or title_from_turns(turns, "Untitled Claude chat")
                shard = make_shard(turns, title, c.get("created_at", ""), existing_ids,
                                   "claude_export", c.get("uuid", ""), "claude", attach)
                if shard:
                    theme_counts[shard["meta_tags"]["theme"]] = theme_counts.get(shard["meta_tags"]["theme"], 0) + 1
                    emit(shard)
            except Exception as e:
                errors += 1
                print(f"  ✗ conv {c.get('uuid','?')}: {e}")

    # 2) design_chats/*.json
    dc_files = sorted(glob.glob(str(in_path / "design_chats" / "*.json")))
    if dc_files:
        print(f"Loading {len(dc_files)} design_chats ...")
        for f in dc_files:
            try:
                d = json.loads(Path(f).read_text(encoding="utf-8"))
                turns, attach = messages_to_turns(d.get("messages", []))
                if len(turns) < min_turns:
                    skipped += 1
                    continue
                title = (d.get("title") or "").strip()
                if not title or title.lower() == "chat":
                    title = title_from_turns(turns, "Claude design chat")
                shard = make_shard(turns, title, d.get("created_at", ""), existing_ids,
                                   "claude_design_chat", d.get("uuid", ""), "claude_design", attach)
                if shard:
                    theme_counts[shard["meta_tags"]["theme"]] = theme_counts.get(shard["meta_tags"]["theme"], 0) + 1
                    emit(shard)
            except Exception as e:
                errors += 1
                print(f"  ✗ design_chat {f}: {e}")

    # 3) memories.json → one profile shard
    mem_path = in_path / "memories.json"
    if mem_path.exists():
        try:
            mem = json.loads(mem_path.read_text(encoding="utf-8"))
            blob = ""
            if isinstance(mem, list) and mem and isinstance(mem[0], dict):
                blob = mem[0].get("conversations_memory", "")
            elif isinstance(mem, dict):
                blob = mem.get("conversations_memory", "")
            if blob.strip():
                turns = [{"timestamp": "", "user": "What does Claude remember about the user?", "ai": blob.strip()}]
                shard = make_shard(turns, "Claude remembered facts about the user", "",
                                   existing_ids, "claude_memories", "memories", "claude")
                if shard:
                    theme_counts[shard["meta_tags"]["theme"]] = theme_counts.get(shard["meta_tags"]["theme"], 0) + 1
                    emit(shard)
        except Exception as e:
            errors += 1
            print(f"  ✗ memories.json: {e}")

    print("─" * 50)
    total = created if not dry_run else sum(theme_counts.values())
    print(f"{'[DRY] would create' if dry_run else '✅ created'}: {total} shards")
    print(f"⏭  skipped (empty/short): {skipped}   ❌ errors: {errors}")
    print("\nTheme breakdown:")
    for theme, c in sorted(theme_counts.items(), key=lambda x: -x[1]):
        print(f"  {theme}: {c}")
    if not dry_run and created:
        print("\nNext: run nova_shard_consolidate to build the index.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Migrate Claude export to NOVA shards")
    p.add_argument("--input", default="../intake/claude shards")
    p.add_argument("--output", default=str(DEFAULT_SHARD_DIR))
    p.add_argument("--min-turns", type=int, default=1)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    migrate(args.input, args.output, args.min_turns, args.dry_run)
