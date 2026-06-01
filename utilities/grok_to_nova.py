"""
grok_to_nova.py — Migrate Grok (xAI) exported conversations to NOVA shards

Grok exports a single backend dump (prod-grok-backend.json) shaped as:
    {
      "conversations": [
        {
          "conversation": {id, title, create_time, asset_ids, ...},
          "responses": [
            {"response": {message, sender: "human"|"ASSISTANT",
                          create_time: {"$date": {"$numberLong": "<ms>"}},
                          file_attachments: [...asset_ids...],
                          agent_thinking_traces, steps, ...}}
          ]
        }, ...
      ],
      "projects": [], "tasks": [], "media_posts": []
    }

We keep `message` text and `sender`, ordered by message create_time. Grok's
reasoning/tool traces (agent_thinking_traces, steps) are dropped as noise.
File attachments are binary image assets (prod-mc-asset-server/) — only their
count is noted, the payloads are not ingested.

Usage:
    python grok_to_nova.py --input "../intake/grok shards" --dry-run
    python grok_to_nova.py --input "../intake/grok shards"
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

BACKEND_GLOB = "prod-grok-backend.json"


# ═══════════════════════════════════════════════════════════
# PARSING
# ═══════════════════════════════════════════════════════════

def mongo_ms(ct) -> int:
    """Extract milliseconds from a Mongo extended-JSON {$date:{$numberLong}}."""
    try:
        return int(ct["$date"]["$numberLong"])
    except (TypeError, KeyError, ValueError):
        return 0


def iso_from_ms(ms: int) -> str:
    if ms <= 0:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def normalize_role(sender: str) -> str:
    s = (sender or "").lower()
    if s in ("human", "user"):
        return "user"
    if s in ("assistant", "grok", "ai", "system"):
        return "assistant"
    return "assistant"


def parse_conversation(item: dict) -> tuple[list[dict], str, str, int]:
    """Return (turns, title, created_at_iso, attachment_count)."""
    conv = item.get("conversation", {}) or {}
    responses = item.get("responses", []) or []

    msgs = []
    attach = 0
    for r in responses:
        resp = (r or {}).get("response", {}) or {}
        text = (resp.get("message") or "").strip()
        attach += len(resp.get("file_attachments") or [])
        if not text:
            continue
        msgs.append({
            "role": normalize_role(resp.get("sender")),
            "content": text,
            "ms": mongo_ms(resp.get("create_time")),
        })

    msgs.sort(key=lambda m: m["ms"])

    turns = []
    i = 0
    while i < len(msgs):
        m = msgs[i]
        if m["role"] == "user":
            ts = iso_from_ms(m["ms"])
            user = m["content"]
            ai = ""
            if i + 1 < len(msgs) and msgs[i + 1]["role"] == "assistant":
                ai = msgs[i + 1]["content"]
                i += 2
            else:
                i += 1
            turns.append({"timestamp": ts, "user": user, "ai": ai})
        else:
            turns.append({"timestamp": iso_from_ms(m["ms"]), "user": "", "ai": m["content"]})
            i += 1

    title = (conv.get("title") or "").strip()
    if not title:
        title = next((t["user"][:80] for t in turns if t.get("user")), "Untitled Grok chat")
    created_at = conv.get("create_time") or (turns[0]["timestamp"] if turns else "")
    return turns, title, created_at, attach


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
        "ai_ml": ["nova", "mcp", "llm", "gpt", "claude", "grok", "embedding", "shard", "agent", "forgemaster", "cogniti", "ai"],
        "technical": ["python", "code", "function", "api", "server", "database", "bug", "error", "c++", "javascript"],
        "career": ["job", "resume", "cv", "interview", "apply", "linkedin", "internship", "salary"],
        "philosophy": ["meaning", "consciousness", "ethics", "philosophy", "theory", "exist", "grief"],
        "research": ["research", "paper", "study", "analysis", "data", "method", "literature", "thesis"],
        "personal": ["feel", "tired", "stressed", "life", "moved", "cluj", "sweden", "family"],
        "creative": ["story", "write", "design", "art", "music", "concept", "idea", "image", "artwork"],
    }
    scores = {t: sum(1 for kw in kws if kw in text) for t, kws in theme_keywords.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "general"


def infer_intent(turns: list[dict]) -> str:
    if not turns:
        return "reflection"
    first = turns[0].get("user", "").lower()
    if any(w in first for w in ["help", "how", "what", "why", "explain", "find", "search", "is there", "??"]):
        return "research"
    if any(w in first for w in ["build", "create", "make", "write", "design"]):
        return "planning"
    if any(w in first for w in ["fix", "error", "bug", "wrong", "broken"]):
        return "brainstorm"
    return "reflection"


def build_shard(turns, title, created_at, existing_ids, conv_id, attach):
    if not turns:
        return None
    theme = infer_theme(title, turns)
    intent = infer_intent(turns)
    clean_title = title.replace("\n", " ").strip()
    base_id = sanitize_filename(f"grok_{theme}_{clean_title[:30]}")
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
        "source": "grok_export",
        "original_title": clean_title,
        "original_id": conv_id,
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


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

def migrate(input_dir: str, output_dir: str, min_turns: int = 1, dry_run: bool = False):
    in_path = Path(input_dir)
    out_path = Path(output_dir)
    print(f"\n{'[DRY RUN] ' if dry_run else ''}NOVA Migration: Grok → Shards")
    print(f"Input:  {in_path}")
    print(f"Output: {out_path}")
    print("─" * 50)

    backends = sorted(in_path.rglob(BACKEND_GLOB))
    if not backends:
        print(f"No {BACKEND_GLOB} found under {in_path}")
        return
    if not dry_run:
        out_path.mkdir(parents=True, exist_ok=True)

    existing_ids = set()
    created = skipped = errors = 0
    theme_counts: dict[str, int] = {}

    for backend in backends:
        print(f"Loading {backend.relative_to(in_path)} ...")
        data = json.loads(backend.read_text(encoding="utf-8"))
        convs = data.get("conversations", []) or []
        print(f"  {len(convs)} conversations")

        for item in convs:
            try:
                turns, title, created_at, attach = parse_conversation(item)
                if len(turns) < min_turns:
                    skipped += 1
                    continue
                conv_id = (item.get("conversation") or {}).get("id", "")
                shard = build_shard(turns, title, created_at, existing_ids, conv_id, attach)
                if shard is None:
                    skipped += 1
                    continue
                theme = shard["meta_tags"]["theme"]
                theme_counts[theme] = theme_counts.get(theme, 0) + 1
                if dry_run:
                    a = f" +{attach} assets" if attach else ""
                    print(f"  [DRY] {shard['shard_id']}.json  ({len(turns)} turns, {theme}{a})")
                else:
                    with open(out_path / f"{shard['shard_id']}.json", "w", encoding="utf-8") as f:
                        json.dump(shard, f, indent=2, ensure_ascii=False)
                    created += 1
            except Exception as e:
                errors += 1
                print(f"  ✗ conversation error: {e}")

    print("─" * 50)
    total = created if not dry_run else sum(theme_counts.values())
    print(f"{'[DRY] would create' if dry_run else '✅ created'}: {total} shards")
    print(f"⏭  skipped (empty/no-turns): {skipped}   ❌ errors: {errors}")
    print("\nTheme breakdown:")
    for theme, c in sorted(theme_counts.items(), key=lambda x: -x[1]):
        print(f"  {theme}: {c}")
    if not dry_run and created:
        print("\nNext: run nova_shard_consolidate to build the index.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Migrate Grok export to NOVA shards")
    p.add_argument("--input", default="../intake/grok shards")
    p.add_argument("--output", default=str(DEFAULT_SHARD_DIR))
    p.add_argument("--min-turns", type=int, default=1)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    migrate(args.input, args.output, args.min_turns, args.dry_run)
