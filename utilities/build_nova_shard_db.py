"""
build_nova_shard_db.py — Populate nova_shard_index.db from all JSON shards.

Run this once after install, or to repair the index after bulk changes:

    cd mcp
    python ../utilities/build_nova_shard_db.py

Flags:
    --stats-only   Print stats without rebuilding
    --db PATH      Override default DB path
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "mcp"))

from nova_shard_db import NovaShardDB, decode_state, NOVA_SHARD_DB_PATH


def main() -> None:
    parser = argparse.ArgumentParser(description="Build NOVA shard SQLite index")
    parser.add_argument("--stats-only", action="store_true", help="Print stats without rebuilding")
    parser.add_argument("--db", default=NOVA_SHARD_DB_PATH, help="Path to SQLite DB")
    args = parser.parse_args()

    with NovaShardDB(args.db) as db:
        if args.stats_only:
            stats = db.stats()
            if not stats:
                print("DB is empty or not yet built.")
            else:
                print(f"Total shards : {stats['total']}")
                print(f"  Confirmed  : {stats['confirmed']}")
                print(f"  Neutral    : {stats['neutral']}")
                print(f"  Contradicted: {stats['contradicted']}")
                print(f"  Quarantined: {stats['quarantined']}")
            return

        print(f"Building index at: {args.db}")
        result = db.rebuild_from_dir()

        if "error" in result:
            print(f"Error: {result['error']}", file=sys.stderr)
            sys.exit(1)

        print(f"Indexed : {result['indexed']} shards")
        if result["failed"]:
            print(f"Failed  : {result['failed']} shards (check logs)")

        stats = db.stats()
        if stats:
            print(f"\nState distribution:")
            print(f"  Confirmed   : {stats['confirmed']}")
            print(f"  Neutral     : {stats['neutral']}")
            print(f"  Contradicted: {stats['contradicted']}")
            print(f"  Quarantined : {stats['quarantined']}")


if __name__ == "__main__":
    main()
