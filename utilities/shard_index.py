"""
shard_index.py — Rebuild NOVA's shard index from disk.

Thin wrapper around ``store.update_index`` so there is one index builder.
This file used to carry its own copy, which had drifted: it read confidence
from the wrong field (every shard indexed at 1.0), skipped .md shards and
wrote the index without the file lock.

Usage (paths default to the repo root; NOVA_DATA_ROOT, NOVA_SHARD_DIR and
NOVA_INDEX_FILE override them as they do for the server):

    python utilities/shard_index.py
"""

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("NOVA_DATA_ROOT", str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "mcp"))

from config import INDEX_FILE, SHARD_DIR  # noqa: E402
from store import update_index as _store_update_index  # noqa: E402

__all__ = ["SHARD_DIR", "INDEX_FILE", "update_index"]


def update_index() -> dict:
    """Rebuild the index from disk and save it. Returns the new index."""
    index = _store_update_index()
    print(f"✓ Index updated with {len(index)} shards → {INDEX_FILE}")
    return index


if __name__ == "__main__":
    update_index()
