"""
nova_hook_extract.py — Claude Code PostToolUse hook.

Receives JSON on stdin:
  {"session_id": "...", "tool_name": "...", "tool_input": {...}, "tool_response": {...}}

On whitelisted tools, creates a session_extracted shard capturing the change.
Quarantine is applied automatically (QUARANTINE_HOURS, QUARANTINE_PENALTY).

Tool whitelist: NOVA_HOOK_TOOLS env var, comma-separated (default: "Edit,Write").
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=False)

from config import SHARD_DIR, QUARANTINE_HOURS
from store import save_shard, patch_index_entry
from graph import add_shard_to_graph
from usage import log_operation


_DEFAULT_WHITELIST = {"Edit", "Write"}


def _whitelisted(tool_name: str) -> bool:
    env = os.environ.get("NOVA_HOOK_TOOLS", "")
    whitelist = {t.strip() for t in env.split(",")} if env else _DEFAULT_WHITELIST
    return tool_name in whitelist


def _build_shard(tool_name: str, tool_input: dict) -> dict:
    file_path = tool_input.get("file_path", tool_input.get("path", "unknown"))

    if tool_name == "Edit":
        snippet = tool_input.get("new_string", "")[:200]
        guiding_q = f"What decision was made when editing {file_path}?"
        description = f"Edited {file_path}: {snippet!r}"
    else:
        snippet = tool_input.get("content", "")[:200]
        guiding_q = f"What was written to {file_path}?"
        description = f"Wrote {file_path}: {snippet!r}"

    shard_id = f"hook_extract_{uuid.uuid4().hex[:12]}"
    now = datetime.now()
    quarantine_until = (now + timedelta(hours=QUARANTINE_HOURS)).isoformat()

    return {
        "shard_id": shard_id,
        "guiding_question": guiding_q,
        "conversation_history": [
            {"user": "PostToolUse hook", "ai": description}
        ],
        "meta_tags": {
            "intent": "research",
            "theme": "session_extracted",
            "usage_count": 0,
            "last_used": now.isoformat(),
            "confidence": 1.0,
            "enrichment_status": "pending",
            "source": "session_extracted",
            "quarantine_until": quarantine_until,
            "summary": description[:150],
        },
        "context": {
            "summary": description[:150],
            "topics": [file_path],
            "conversation_type": "hook_extracted",
            "embedding": [],
            "last_context_update": now.isoformat(),
        },
    }


def main() -> None:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    tool_name = payload.get("tool_name", "")
    if not _whitelisted(tool_name):
        sys.exit(0)

    tool_input = payload.get("tool_input", {})
    shard = _build_shard(tool_name, tool_input)
    shard_id = shard["shard_id"]

    shard_path = str(Path(SHARD_DIR) / f"{shard_id}.json")
    save_shard(shard_path, shard)
    patch_index_entry(shard_id, shard)
    add_shard_to_graph(shard_id, shard)

    log_operation("hook_extract", [shard_id], {
        "source": "PostToolUse",
        "tool_name": tool_name,
        "file_path": tool_input.get("file_path", ""),
        "quarantine_until": shard["meta_tags"]["quarantine_until"],
    })


if __name__ == "__main__":
    main()
