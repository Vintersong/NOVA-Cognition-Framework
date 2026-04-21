"""
usage.py — Operation logging for NOVA.

Appends structured JSONL entries for every tool invocation.
Extracted from nova_server.py so the log path is configurable centrally.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime
from typing import Optional

from config import USAGE_LOG_FILE

logger = logging.getLogger(__name__)
_error_counts: Counter[str] = Counter()

def log_operation(tool_name: str, shard_ids: list[str], metadata: Optional[dict] = None):
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
    except Exception as exc:
        _error_counts["log_operation"] += 1
        logger.warning("usage.log_operation failed (%s): %s", type(exc).__name__, exc)
