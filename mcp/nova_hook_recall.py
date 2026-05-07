"""
nova_hook_recall.py — Claude Code UserPromptSubmit hook.

Receives JSON on stdin:
  {"session_id": "...", "prompt": "...", "cwd": "..."}

Calls hook_recall() and writes injected context to stdout as JSON:
  {"context": "[NOVA RECALL]\n- shard_id ..."}

Exits 0 always — never blocks the prompt.
Logs every injection to nova_usage.jsonl.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# mcp/ dir is this file's parent — put it on sys.path so sibling modules load.
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=False)

from recall import hook_recall
from usage import log_operation


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--min-confidence", type=float, default=0.85)
    args = parser.parse_args()

    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    prompt = payload.get("prompt", "")
    if not prompt:
        sys.exit(0)

    results = hook_recall(prompt, top_k=args.top_k, min_confidence=args.min_confidence)
    if not results:
        sys.exit(0)

    lines = ["[NOVA RECALL]"]
    for r in results:
        lines.append(
            f"- {r['shard_id']} (conf={r['confidence']:.2f}): {r['summary']}"
        )
    context = "\n".join(lines)

    log_operation("hook_recall", [r["shard_id"] for r in results], {
        "source": "UserPromptSubmit",
        "prompt_chars": len(prompt),
        "injected_count": len(results),
    })

    print(json.dumps({"context": context}))


if __name__ == "__main__":
    main()
