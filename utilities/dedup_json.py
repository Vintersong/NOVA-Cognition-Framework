"""
dedup_json.py — Duplicate shard detection and removal for NOVA.

Hashes shard message content (author + content pairs) to find
exact duplicates. Deletes duplicates by default.

Usage:
    python dedup_json.py              # Find and delete duplicates
    python dedup_json.py --dry-run    # Report only, don't delete
"""

import os
import sys
import json
import hashlib

from shard_index import SHARD_DIR, update_index


def normalize_content(data: dict) -> str | None:
    """Hash the meaningful message content of a shard."""
    if "messages" not in data and "conversation_history" not in data:
        return None

    try:
        # Support both message formats
        messages = data.get("messages") or data.get("conversation_history", [])
        if data.get("conversation_history"):
            core = [(m.get("user", "").strip(), m.get("ai", "").strip()) for m in messages]
        else:
            core = [(m.get("author", "").strip(), m.get("content", "").strip()) for m in messages]

        core_str = json.dumps(core, sort_keys=True)
        return hashlib.md5(core_str.encode("utf-8")).hexdigest()
    except Exception:
        return None


def find_duplicates() -> list[tuple[str, str]]:
    """Returns list of (duplicate_path, original_filename) tuples."""
    seen_hashes = {}
    duplicates = []

    for filename in sorted(os.listdir(SHARD_DIR)):
        if not filename.endswith(".json"):
            continue

        path = os.path.join(SHARD_DIR, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                content_hash = normalize_content(data)
                if content_hash:
                    if content_hash in seen_hashes:
                        duplicates.append((path, seen_hashes[content_hash]))
                    else:
                        seen_hashes[content_hash] = filename
        except Exception as e:
            print(f"✗ Error reading {filename}: {e}")

    return duplicates


def main():
    dry_run = "--dry-run" in sys.argv
    duplicates = find_duplicates()

    if not duplicates:
        print("✓ No duplicates found.")
        return

    print(f"Found {len(duplicates)} duplicate(s):\n")
    for dup_path, original in duplicates:
        dup_name = os.path.basename(dup_path)
        print(f"  {dup_name}  ==  {original}")

    if dry_run:
        print(f"\n(Dry run — no files deleted)")
        return

    print()
    for dup_path, original in duplicates:
        try:
            os.remove(dup_path)
            print(f"  ✓ Deleted: {os.path.basename(dup_path)}")
        except Exception as e:
            print(f"  ✗ Error deleting {dup_path}: {e}")

    # Rebuild index after cleanup
    update_index()


if __name__ == "__main__":
    main()
