"""
nova_hook_stop.py — Claude Code Stop hook.

Checks if any shard was written since the session started (60-minute window).
If not, prints a handoff reminder so the user knows to call nova_shard_update
before ending the session.

Output format (printed to stdout as JSON):
  {"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": "..."}}

Exit 0 always — Stop hooks cannot block.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=False)

from config import SHARD_DIR

_WINDOW_SECONDS = 3600  # 1 hour — if no shard written in this window, remind


def _newest_shard_age_seconds(shard_dir: str) -> float | None:
    """Return seconds since the most recently modified shard, or None if dir empty."""
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
        json.loads(raw)  # validate payload but we don't need fields
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    age = _newest_shard_age_seconds(SHARD_DIR)

    if age is None or age > _WINDOW_SECONDS:
        print(
            "⚠ NOVA handoff: no shard written in the last hour. "
            "Run nova_shard_update to persist session decisions before ending. "
            "See CLAUDE.md Session Handoff Protocol.",
            file=sys.stderr,
        )

    sys.exit(0)


if __name__ == "__main__":
    main()
