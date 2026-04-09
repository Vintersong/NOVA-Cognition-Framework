"""
chatgpt_to_nova.py — Migrate ChatGPT exported conversations to NOVA shards

Reads ChatGPT export JSON files (conversations-000.json etc.) and converts
each conversation into a NOVA shard JSON file.

Usage:
    python chatgpt_to_nova.py --input ./chatgpt_export

Options:
    --input     Directory containing ChatGPT export JSON files (default: ./chatgpt_export)
    --output    Directory to write NOVA shards (default: repo shards/ dir, override with NOVA_SHARD_DIR)
    --min-turns Minimum conversation turns to include (default: 2, skips tiny convos)
    --dry-run   Preview what would be created without writing files
"""

import os
import sys
import json
import re
import argparse
from datetime import datetime, timezone
from pathlib import Path

# Default shard output directory — mirrors utilities/autoresearch.py convention.
# Resolves to <repo>/shards regardless of where the script is run from.
DEFAULT_SHARD_DIR = Path(os.getenv(
    "NOVA_SHARD_DIR",
    Path(__file__).parent.parent / "shards"
))


# ═══════════════════════════════════════════════════════════
# CONVERSATION EXTRACTION
# ═══════════════════════════════════════════════════════════

def extract_conversation_title(conv: dict) -> str:
    """Get the conversation title or derive one from the first message."""
    title = conv.get("title", "").strip()
    if title and title not in ("New conversation", "Untitled"):
        return title

    # Derive from first user message
    mapping = conv.get("mapping", {})
    for node in mapping.values():
        msg = node.get("message")
        if not msg:
            continue
        if msg.get("author", {}).get("role") == "user":
            content = extract_content(msg)
            if content:
                return content[:60].strip()

    return "Untitled Conversation"


def extract_content(message: dict) -> str:
    """Extract text content from a ChatGPT message node."""
    if not message:
        return ""

    content = message.get("content", {})
    if not content:
        return ""

    content_type = content.get("content_type", "")

    if content_type == "text":
        parts = content.get("parts", [])
        text_parts = []
        for part in parts:
            if isinstance(part, str):
                text_parts.append(part)
            elif isinstance(part, dict) and part.get("content_type") == "text":
                text_parts.append(part.get("text", ""))
        return "\n".join(text_parts).strip()

    elif content_type == "multimodal_text":
        parts = content.get("parts", [])
        text_parts = [p for p in parts if isinstance(p, str)]
        return "\n".join(text_parts).strip()

    return ""


def build_linear_history(conv: dict) -> list[dict]:
    """
    Walk the conversation tree to extract a linear history of user/assistant turns.
    ChatGPT stores conversations as a tree (for branches/edits) — we follow the
    main branch by walking from root to current_node.
    """
    mapping = conv.get("mapping", {})
    current_node_id = conv.get("current_node")

    if not current_node_id or not mapping:
        return []

    # Walk backwards from current_node to root, collecting node IDs
    path = []
    node_id = current_node_id
    visited = set()

    while node_id and node_id not in visited:
        visited.add(node_id)
        path.append(node_id)
        node = mapping.get(node_id, {})
        node_id = node.get("parent")

    path.reverse()  # root to current

    # Extract messages along the path
    history = []
    for nid in path:
        node = mapping.get(nid, {})
        msg = node.get("message")
        if not msg:
            continue

        role = msg.get("author", {}).get("role", "")
        if role not in ("user", "assistant"):
            continue

        content = extract_content(msg)
        if not content:
            continue

        create_time = msg.get("create_time")
        if create_time:
            ts = datetime.fromtimestamp(create_time, tz=timezone.utc).isoformat()
        else:
            ts = datetime.now(tz=timezone.utc).isoformat()

        history.append({
            "role": role,
            "content": content,
            "timestamp": ts
        })

    return history


def history_to_shard_format(history: list[dict]) -> list[dict]:
    """Convert flat role/content history to NOVA shard conversation_history format."""
    turns = []
    i = 0

    while i < len(history):
        entry = history[i]

        if entry["role"] == "user":
            user_msg = entry["content"]
            ai_msg = ""
            ts = entry["timestamp"]

            # Check if next message is assistant
            if i + 1 < len(history) and history[i + 1]["role"] == "assistant":
                ai_msg = history[i + 1]["content"]
                i += 2
            else:
                i += 1

            turns.append({
                "timestamp": ts,
                "user": user_msg,
                "ai": ai_msg
            })
        else:
            # Orphan assistant message (shouldn't happen often)
            turns.append({
                "timestamp": entry["timestamp"],
                "user": "",
                "ai": entry["content"]
            })
            i += 1

    return turns


# ═══════════════════════════════════════════════════════════
# SHARD CREATION
# ═══════════════════════════════════════════════════════════

def sanitize_filename(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r'[^a-z0-9_]+', '_', name)
    name = re.sub(r'_+', '_', name)
    return name[:50].strip('_')


def infer_theme(title: str, history: list[dict]) -> str:
    """Infer a theme from conversation title and content."""
    text = (title + " " + " ".join(
        t.get("user", "") for t in history[:3]
    )).lower()

    theme_keywords = {
        "game_design": ["game", "mechanic", "level", "player", "unity", "godot", "love2d", "lua", "sgdk", "shard"],
        "ai_ml": ["nova", "mcp", "llm", "gpt", "claude", "embedding", "shard", "agent", "forgemaster", "ai"],
        "technical": ["python", "code", "function", "api", "server", "database", "bug", "error", "c++", "javascript"],
        "career": ["job", "resume", "cv", "interview", "apply", "linkedin", "internship", "salary"],
        "philosophy": ["meaning", "consciousness", "ethics", "philosophy", "theory", "exist"],
        "research": ["research", "paper", "study", "analysis", "data", "method"],
        "personal": ["feel", "tired", "stressed", "life", "moved", "cluj", "sweden", "family"],
        "creative": ["story", "write", "design", "art", "music", "concept", "idea"],
    }

    scores = {}
    for theme, keywords in theme_keywords.items():
        scores[theme] = sum(1 for kw in keywords if kw in text)

    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "general"


def infer_intent(history: list[dict]) -> str:
    """Infer intent from conversation content."""
    if not history:
        return "reflection"

    first_user = history[0].get("user", "").lower()

    if any(w in first_user for w in ["help", "how", "what", "why", "explain", "??"]):
        return "research"
    if any(w in first_user for w in ["build", "create", "make", "write", "design"]):
        return "planning"
    if any(w in first_user for w in ["fix", "error", "bug", "wrong", "broken"]):
        return "brainstorm"

    return "reflection"


def conversation_to_shard(conv: dict, existing_ids: set) -> dict | None:
    """Convert a single ChatGPT conversation to a NOVA shard."""
    title = extract_conversation_title(conv)
    history_raw = build_linear_history(conv)
    history = history_to_shard_format(history_raw)

    if not history:
        return None

    theme = infer_theme(title, history)
    intent = infer_intent(history)

    base_id = sanitize_filename(f"chatgpt_{theme}_{title[:30]}")

    # Ensure unique shard_id
    shard_id = base_id
    counter = 1
    while shard_id in existing_ids:
        shard_id = f"{base_id}_{counter}"
        counter += 1

    existing_ids.add(shard_id)

    create_time = conv.get("create_time")
    if create_time:
        created_at = datetime.fromtimestamp(create_time, tz=timezone.utc).isoformat()
    else:
        created_at = datetime.now(tz=timezone.utc).isoformat()

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
            "source": "chatgpt_export",
            "original_title": title,
            "original_id": conv.get("id", ""),
            "imported_at": datetime.now(tz=timezone.utc).isoformat()
        }
    }


# ═══════════════════════════════════════════════════════════
# MAIN MIGRATION
# ═══════════════════════════════════════════════════════════

def load_conversations(input_dir: str) -> list[dict]:
    """Load all ChatGPT export JSON files from the input directory."""
    conversations = []
    input_path = Path(input_dir)

    json_files = sorted(input_path.glob("conversations-*.json"))
    if not json_files:
        # Try any JSON files
        json_files = sorted(input_path.glob("*.json"))

    print(f"Found {len(json_files)} JSON file(s) in {input_dir}")

    for filepath in json_files:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, list):
                conversations.extend(data)
                print(f"  ✓ {filepath.name}: {len(data)} conversations")
            elif isinstance(data, dict):
                conversations.append(data)
                print(f"  ✓ {filepath.name}: 1 conversation")
        except Exception as e:
            print(f"  ✗ {filepath.name}: failed to load — {e}")

    return conversations


def migrate(input_dir: str, output_dir: str, min_turns: int = 2, dry_run: bool = False):
    """Main migration function."""
    print(f"\n{'[DRY RUN] ' if dry_run else ''}NOVA Migration: ChatGPT → Shards")
    print(f"Input:  {input_dir}")
    print(f"Output: {output_dir}")
    print(f"Min turns: {min_turns}")
    print("─" * 50)

    # Load all conversations
    conversations = load_conversations(input_dir)
    print(f"\nTotal conversations loaded: {len(conversations)}")

    if not conversations:
        print("No conversations found. Check your input directory.")
        return

    # Create output directory
    output_path = Path(output_dir)
    if not dry_run:
        output_path.mkdir(parents=True, exist_ok=True)

    # Convert each conversation
    existing_ids = set()
    created = 0
    skipped_empty = 0
    skipped_short = 0
    errors = 0

    theme_counts = {}

    for i, conv in enumerate(conversations):
        try:
            shard = conversation_to_shard(conv, existing_ids)

            if shard is None:
                skipped_empty += 1
                continue

            turn_count = len(shard["conversation_history"])
            if turn_count < min_turns:
                skipped_short += 1
                continue

            theme = shard["meta_tags"]["theme"]
            theme_counts[theme] = theme_counts.get(theme, 0) + 1

            if dry_run:
                print(f"  [DRY] Would create: {shard['shard_id']}.json ({turn_count} turns, theme: {theme})")
            else:
                filepath = output_path / f"{shard['shard_id']}.json"
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(shard, f, indent=2, ensure_ascii=False)
                created += 1

                if created % 50 == 0:
                    print(f"  Progress: {created} shards created...")

        except Exception as e:
            errors += 1
            print(f"  ✗ Error on conversation {i}: {e}")

    # Summary
    print(f"\n{'─' * 50}")
    if dry_run:
        total = len(conversations) - skipped_empty - skipped_short
        print(f"[DRY RUN] Would create: {total} shards")
    else:
        print(f"✅ Created: {created} shards")

    print(f"⏭  Skipped (empty): {skipped_empty}")
    print(f"⏭  Skipped (too short, < {min_turns} turns): {skipped_short}")
    if errors:
        print(f"❌ Errors: {errors}")

    print(f"\nTheme breakdown:")
    for theme, count in sorted(theme_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"  {theme}: {count}")

    if not dry_run and created > 0:
        print(f"\nShards written to: {output_dir}")
        print("Next step: run nova_shard_consolidate to build the index and check merge candidates.")


# ═══════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate ChatGPT export to NOVA shards")
    parser.add_argument("--input", default="./chatgpt_export", help="Input directory with ChatGPT JSON exports")
    parser.add_argument("--output", default=str(DEFAULT_SHARD_DIR), help="Output directory for NOVA shards (default: repo shards/ dir)")
    parser.add_argument("--min-turns", type=int, default=2, help="Minimum conversation turns to include")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing files")

    args = parser.parse_args()

    migrate(
        input_dir=args.input,
        output_dir=args.output,
        min_turns=args.min_turns,
        dry_run=args.dry_run
    )
