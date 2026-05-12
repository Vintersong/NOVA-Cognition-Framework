"""
export_obsidian.py — Export NOVA shards to an Obsidian vault.

    cd mcp
    python ../utilities/export_obsidian.py
    python ../utilities/export_obsidian.py --out D:/my-vault
    python ../utilities/export_obsidian.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "mcp"))

from store import load_index, update_index, load_shard
from graph import load_graph
from obsidian_export import export_shards, OBSIDIAN_DIR


def main() -> None:
    parser = argparse.ArgumentParser(description="Export NOVA shards to Obsidian vault")
    parser.add_argument("--out", default=OBSIDIAN_DIR, help="Output directory")
    parser.add_argument("--dry-run", action="store_true", help="Count without writing")
    args = parser.parse_args()

    index = load_index() or update_index()
    graph = load_graph()

    if args.dry_run:
        skippable = sum(
            1 for e in index.values()
            if "forgotten" in e.get("tags", []) or "archived" in e.get("tags", [])
        )
        print(f"Would export : {len(index) - skippable} shards")
        print(f"Would skip   : {skippable} (archived/forgotten)")
        print(f"Output dir   : {args.out}")
        return

    print(f"Exporting {len(index)} shards to {args.out} ...")
    result = export_shards(index, load_shard, graph, args.out)

    print(f"Exported : {result['exported']}")
    print(f"Skipped  : {result['skipped']}")
    if result["errors"]:
        print(f"Errors   : {len(result['errors'])}")
        for e in result["errors"][:5]:
            print(f"  {e}")
    print(f"Index    : {args.out}/_NOVA_INDEX.md")


if __name__ == "__main__":
    main()
