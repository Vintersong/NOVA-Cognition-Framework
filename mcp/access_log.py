"""
access_log.py — Shard access logging for NOVA.

log_shard_access() is called by every retrieval path:
  nova_shard_search, nova_shard_interact (nova_server.py)
  hook_recall (recall.py)

One JSONL line per access:
  {"timestamp": "...", "shard_id": "...", "source": "..."}

read_access_log() is used by NÓTT's decay-on-read pass.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from config import ACCESS_LOG_FILE
from timeutils import parse_iso, now_utc

logger = logging.getLogger(__name__)


def log_shard_access(shard_id: str, source: str) -> None:
    """Append one access record. Never raises — retrieval must not be blocked."""
    entry = {
        "timestamp": now_utc().isoformat(),
        "shard_id": shard_id,
        "source": source,
    }
    try:
        with open(ACCESS_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as exc:
        logger.warning("access_log.log_shard_access failed: %s", exc)


def read_access_log(window_days: int) -> dict[str, list[str]]:
    """
    Return {shard_id: [timestamp, ...]} for accesses within the last window_days.
    Empty dict if the log doesn't exist yet.
    """
    cutoff = now_utc() - timedelta(days=window_days)
    result: dict[str, list[str]] = {}
    try:
        with open(ACCESS_LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ts = parse_iso(entry["timestamp"])
                    if ts is not None and ts >= cutoff:
                        sid = entry["shard_id"]
                        result.setdefault(sid, []).append(entry["timestamp"])
                except (json.JSONDecodeError, KeyError):
                    continue
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("access_log.read_access_log failed: %s", exc)
    return result
