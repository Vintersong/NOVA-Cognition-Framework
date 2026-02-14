"""
rename_shards.py — Filename normalization for NOVA shards.

Renames shard files to match a sanitized version of their shard_id,
theme, or guiding question. One-time migration tool.

Usage:
    python rename_shards.py              # Rename files
    python rename_shards.py --dry-run    # Preview renames only
"""

import os
import re
import sys
import json

from shard_index import SHARD_DIR, update_index


def sanitize_filename(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r'[^a-z0-9_]+', '_', name)
    return name[:40]


def main():
    dry_run = "--dry-run" in sys.argv
    renamed = 0

    for filename in sorted(os.listdir(SHARD_DIR)):
        if not filename.endswith(".json"):
            continue

        old_path = os.path.join(SHARD_DIR, filename)
        try:
            with open(old_path, "r", encoding="utf-8", errors="ignore") as f:
                data = json.load(f)

            # Derive base name from shard data
            base = None
            if "shard_id" in data:
                base = data["shard_id"]
            elif "meta_tags" in data and "theme" in data["meta_tags"]:
                base = data["meta_tags"]["theme"]
            elif "guiding_question" in data:
                base = data["guiding_question"]
            else:
                base = filename.replace(".json", "")

            new_name = sanitize_filename(base) + ".json"
            new_path = os.path.join(SHARD_DIR, new_name)

            # Avoid overwrite conflicts
            counter = 1
            while os.path.exists(new_path) and new_name != filename:
                new_name = f"{sanitize_filename(base)}_{counter}.json"
                new_path = os.path.join(SHARD_DIR, new_name)
                counter += 1

            if new_name != filename:
                if dry_run:
                    print(f"  Would rename: {filename} → {new_name}")
                else:
                    os.rename(old_path, new_path)
                    print(f"  ✓ Renamed: {filename} → {new_name}")
                renamed += 1

        except Exception as e:
            print(f"  ✗ Error processing {filename}: {e}")

    if renamed == 0:
        print("✓ All filenames already normalized.")
    elif dry_run:
        print(f"\n(Dry run — {renamed} file(s) would be renamed)")
    else:
        print(f"\n✓ Renamed {renamed} file(s)")
        update_index()


if __name__ == "__main__":
    main()
