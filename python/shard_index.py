"""
shard_index.py — Unified shard index manager for NOVA.

Maintains a JSON index of all shards with metadata, usage tags,
and guiding questions. Used by main.py for shard discovery and
by the MCP server for search/list operations.
"""

import os
import json
from datetime import datetime, timedelta

SHARD_DIR = os.environ.get("NOVA_SHARD_DIR", "shards")
INDEX_FILE = os.environ.get("NOVA_INDEX_FILE", "shard_index.json")


def load_shard_file(filepath: str) -> dict | None:
    """Load a single shard JSON file. Returns None on failure."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"✗ Failed to load {filepath}: {e}")
        return None


def load_index() -> dict:
    """Load the shard index from disk. Returns empty dict if missing."""
    if not os.path.exists(INDEX_FILE):
        return {}
    try:
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Migrate legacy list-based index to dict-based
            if isinstance(data, dict) and "shards" in data and isinstance(data["shards"], list):
                return _migrate_legacy_index()
            return data
    except Exception:
        return {}


def save_index(index: dict):
    """Write the shard index to disk."""
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)


def classify_tags(shard: dict) -> list[str]:
    """Generate status tags based on shard usage patterns."""
    tags = []
    now = datetime.now()
    meta = shard.get("meta_tags", {})

    usage_count = meta.get("usage_count", 0)
    last_used_str = meta.get("last_used")

    if last_used_str:
        try:
            last_used = datetime.fromisoformat(last_used_str)
            if now - last_used < timedelta(days=3):
                tags.append("recent")
            if now - last_used > timedelta(days=14):
                tags.append("stale")
        except (ValueError, TypeError):
            pass

    if usage_count > 10:
        tags.append("frequently_used")

    if meta.get("intent") == "archived":
        tags.append("archived")

    # Check if context_extractor has enriched this shard
    if shard.get("context", {}).get("embedding"):
        tags.append("enriched")

    return tags


def build_index() -> dict:
    """Scan the shards directory and build a complete index."""
    index = {}

    if not os.path.exists(SHARD_DIR):
        return index

    for fname in sorted(os.listdir(SHARD_DIR)):
        if not fname.endswith(".json"):
            continue

        fpath = os.path.join(SHARD_DIR, fname)
        shard = load_shard_file(fpath)
        if not shard:
            continue

        shard_id = shard.get("shard_id", fname.replace(".json", ""))

        index[shard_id] = {
            "shard_id": shard_id,
            "filename": fname,
            "guiding_question": shard.get("guiding_question", ""),
            "tags": classify_tags(shard),
            "meta": shard.get("meta_tags", {}),
            "context_summary": shard.get("context", {}).get("summary", ""),
            "context_topics": shard.get("context", {}).get("topics", []),
        }

    return index


def update_index() -> dict:
    """Rebuild the index from disk and save it. Returns the new index."""
    index = build_index()
    save_index(index)
    print(f"✓ Index updated with {len(index)} shards → {INDEX_FILE}")
    return index


def _migrate_legacy_index() -> dict:
    """Convert old list-based index format to new dict-based format."""
    print("⚙ Migrating legacy index format...")
    new_index = build_index()
    save_index(new_index)
    return new_index


if __name__ == "__main__":
    update_index()
