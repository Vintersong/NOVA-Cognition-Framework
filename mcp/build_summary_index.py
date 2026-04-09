"""Batch-build summary_index.json and summary_index.md using metadata-only shard reads.

Usage:
    cd mcp
    python build_summary_index.py

The script is resumable. Existing summaries are preserved and only missing
summary sentences are sent to Haiku in random batches of five.
"""

from __future__ import annotations

import json

from store import load_summary_index, rebuild_summary_indexes


def main() -> None:
    summary_index = load_summary_index()
    existing = len(summary_index.get("shards", {}))
    updated = rebuild_summary_indexes(generate_missing=True, batch_size=5)
    total = len(updated.get("shards", {}))
    populated = sum(1 for entry in updated.get("shards", {}).values() if entry.get("d"))
    print(json.dumps({
        "status": "ok",
        "existing_entries": existing,
        "total_entries": total,
        "summaries_present": populated,
    }, indent=2))


if __name__ == "__main__":
    main()