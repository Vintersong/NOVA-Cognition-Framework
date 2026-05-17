"""
backfill_graph_entities.py — Register every indexed shard as a graph entity.

The knowledge graph (`shard_graph.json`) only contains entities for shards
created through `nova_shard_create`. Older / imported shards (ChatGPT export,
direct JSON drops, pre-graph corpus) never went through that path, so they
sit in the index but not in `graph["entities"]`.

The NÓTT scheduled cluster pass assigns `cluster_id` only to entities present
in the graph. With most shards missing as entities, the cluster-aware hook
recall has nothing to collapse against — over 95% of recalls return shards
with no `cluster_id`.

This script walks the shard index and calls `add_shard_to_graph` for any
shard not yet registered. Run once, then trigger a fresh cluster pass via
`nova_shard_consolidate` (or wait for the next SCHEDULED NÓTT cycle).

Usage:
    python utilities/backfill_graph_entities.py [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

_MCP_DIR = Path(__file__).resolve().parent.parent / "mcp"
sys.path.insert(0, str(_MCP_DIR))

from store import load_index, load_shard  # type: ignore[import-not-found]
from graph import load_graph, save_graph  # type: ignore[import-not-found]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be added without writing the graph.",
    )
    args = parser.parse_args()

    index = load_index()
    graph = load_graph()
    existing = set(graph.get("entities", {}).keys())
    missing = [sid for sid in index if sid not in existing]

    print(f"Indexed shards: {len(index)}")
    print(f"Already entities: {len(existing)}")
    print(f"Missing entities: {len(missing)}")

    if not missing:
        print("Nothing to do.")
        return 0

    if args.dry_run:
        print("--dry-run set; no writes.")
        for sid in missing[:10]:
            print(f"  would add: {sid}")
        if len(missing) > 10:
            print(f"  ... and {len(missing) - 10} more")
        return 0

    failed: list[tuple[str, str]] = []
    for i, shard_id in enumerate(missing, start=1):
        try:
            data, _ = load_shard(shard_id)
        except FileNotFoundError:
            failed.append((shard_id, "shard file missing"))
            continue
        except Exception as exc:
            failed.append((shard_id, f"load failed: {exc}"))
            continue
        try:
            graph["entities"][shard_id] = {
                "type": "Shard",
                "guiding_question": data.get("guiding_question", ""),
                "theme": data.get("meta_tags", {}).get("theme", "general"),
                "intent": data.get("meta_tags", {}).get("intent", "reflection"),
                "created_at": datetime.now().isoformat(),
                "confidence": data.get("meta_tags", {}).get("confidence", 1.0),
            }
        except Exception as exc:
            failed.append((shard_id, f"graph write failed: {exc}"))
            continue
        if i % 50 == 0:
            print(f"  progress: {i}/{len(missing)}")

    if len(missing) > len(failed):
        save_graph(graph)
    added = len(missing) - len(failed)
    print(f"Registered {added} new graph entities.")
    if failed:
        print(f"Skipped {len(failed)} shards:")
        for sid, reason in failed[:20]:
            print(f"  - {sid}: {reason}")
        if len(failed) > 20:
            print(f"  ... and {len(failed) - 20} more")

    print()
    print("Next: trigger nova_shard_consolidate to recompute clusters across")
    print("the full corpus, or wait for the next SCHEDULED NOTT cycle.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
