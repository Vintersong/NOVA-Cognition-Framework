"""
convert_shards_to_md.py — Batch-convert JSON shards to YAML+MD format.

Safe to run at any time — JSON files are only deleted after a successful write.
Already-.md shards are skipped. Run with --dry-run first to preview.

    cd mcp
    python ../utilities/convert_shards_to_md.py --dry-run
    python ../utilities/convert_shards_to_md.py
    python ../utilities/convert_shards_to_md.py --limit 50   # incremental batches
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "mcp"))

from config import SHARD_DIR
from shard_format import convert_shard_file, md_path_for


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert NOVA JSON shards to YAML+MD")
    parser.add_argument("--dry-run", action="store_true", help="List shards to convert without writing")
    parser.add_argument("--limit", type=int, default=0, help="Max shards to convert (0 = all)")
    parser.add_argument("--keep-json", action="store_true", help="Write .md but don't delete .json")
    args = parser.parse_args()

    root = Path(SHARD_DIR)
    json_files = sorted(p for p in root.glob("*.json") if not p.name.endswith(".lock"))
    # Skip any that already have a .md counterpart
    to_convert = [p for p in json_files if not md_path_for(p).exists()]

    if args.limit > 0:
        to_convert = to_convert[:args.limit]

    print(f"JSON shards total   : {len(json_files)}")
    print(f"Already .md         : {len(json_files) - len([p for p in json_files if not md_path_for(p).exists()])}")
    print(f"To convert          : {len(to_convert)}")

    if args.dry_run:
        for p in to_convert[:10]:
            print(f"  would convert: {p.name}")
        if len(to_convert) > 10:
            print(f"  ... and {len(to_convert) - 10} more")
        return

    converted = failed = 0
    for path in to_convert:
        try:
            out = convert_shard_file(path, delete_json=not args.keep_json)
            converted += 1
        except Exception as exc:
            print(f"  FAILED {path.name}: {exc}", file=sys.stderr)
            failed += 1

    print(f"Converted : {converted}")
    if failed:
        print(f"Failed    : {failed}")


if __name__ == "__main__":
    main()
