"""
nova_hook_precompact.py — Claude Code PreCompact hook.

Blocks context compaction if no shard has been written in the current session
window (30 minutes), enforcing the session handoff protocol from CLAUDE.md.

The logic: if Claude is about to compact context and no NOVA write happened
recently, the session decisions haven't been persisted. Block with a clear
reason so the model writes the handoff first.

Output to block:
  {"decision": "block", "reason": "..."}

Output to allow (shard written recently):
  {"decision": "allow"}

Exit 0 always.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=False)

from config import SHARD_DIR

_BLOCK_WINDOW_SECONDS = 1800  # 30 minutes


def _newest_shard_age_seconds(shard_dir: str) -> float | None:
    root = Path(shard_dir)
    if not root.is_dir():
        return None
    newest = max(
        (p.stat().st_mtime for p in root.glob("*.json")),
        default=None,
    )
    if newest is None:
        return None
    return time.time() - newest


def main() -> None:
    raw = sys.stdin.read()
    try:
        json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    age = _newest_shard_age_seconds(SHARD_DIR)

    if age is None or age > _BLOCK_WINDOW_SECONDS:
        print(json.dumps({
            "decision": "block",
            "reason": (
                "NOVA session handoff not detected. Write decisions to NOVA before "
                "compaction: nova_shard_update(shard_id=..., user_message='Session handoff', "
                "ai_response='CURRENT STATE: ... NEXT ACTION: ...'). "
                "See CLAUDE.md Session Handoff Protocol."
            ),
        }))

    sys.exit(0)


if __name__ == "__main__":
    main()
