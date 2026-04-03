"""
usage.py — Operation logging for NOVA.

Appends structured JSONL entries for every tool invocation.
Extracted from nova_server.py so the log path is configurable centrally.
"""

from __future__ import annotations

import json
from datetime import datetime

from config import USAGE_LOG_FILE


def log_operation(tool_name: str, shard_ids: list[str], metadata: dict = None):
    """Append operation log to JSONL file for cost/usage analysis."""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "tool": tool_name,
        "shards": shard_ids,
        "metadata": metadata or {},
    }
    try:
        with open(USAGE_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass
