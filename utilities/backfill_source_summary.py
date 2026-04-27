"""
backfill_source_summary.py — Step 1 migration script.

Adds `source` and `summary` fields to meta_tags on all existing shards.

Source assignment rules (first match wins):
  - meta_tags.source already in the new enum -> keep as-is (idempotent)
  - meta_tags.source == "chatgpt_export" OR meta_tags.imported_at present -> "external_doc"
  - shard_data has a "nidhogg" list with entries -> shard was enriched by nidhogg, origin still "agent_inference"
  - default -> "agent_inference"

Summary generation:
  - Calls Haiku with a prompt-cached system prompt (~50 tokens per summary).
  - Skips shards that already have a non-empty meta_tags.summary.
  - Falls back to the first 200 chars of the guiding question if CLAUDE_API_KEY is absent.

Usage:
  cd mcp && python ../utilities/backfill_source_summary.py [--dry-run] [--skip-summaries]

Flags:
  --dry-run         Print what would change, write nothing.
  --skip-summaries  Assign source fields only; skip Haiku summary generation.
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Allow importing from mcp/ when run from repo root or utilities/
_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT / "mcp"))

from dotenv import load_dotenv
load_dotenv(dotenv_path=_REPO_ROOT / ".env")

from store import load_shard, save_shard, load_index, update_index
from config import SHARD_DIR, CLAUDE_API_KEY, HUGINN_MODEL

_VALID_SOURCES = {"user_input", "external_doc", "agent_inference", "session_extracted", "corroborated_by"}

_SUMMARY_SYSTEM = (
    "You produce a single-sentence memory summary (~50 tokens) of a conversation shard. "
    "The summary must capture the core topic and any key conclusion. "
    "Return ONLY the summary sentence — no preamble, no punctuation beyond one period."
)


def _infer_source(shard: dict) -> str:
    meta = shard.get("meta_tags", {})
    existing = meta.get("source", "")
    if existing in _VALID_SOURCES:
        return existing
    if existing == "chatgpt_export" or "imported_at" in meta or "original_id" in meta:
        return "external_doc"
    return "agent_inference"


def _summarise_via_haiku(shard: dict) -> str:
    """Call Haiku with prompt caching to generate a ~50-token summary."""
    import anthropic
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

    guiding_q = shard.get("guiding_question", "")
    history = shard.get("conversation_history") or shard.get("turns") or []
    # Build a compact excerpt: first user turn + last AI turn (capped at 500 chars each)
    first_user = ""
    last_ai = ""
    for turn in history:
        u = turn.get("user", "")
        if u and not first_user:
            first_user = u[:500]
        a = turn.get("ai", "")
        if a:
            last_ai = a[:500]

    content = f"Guiding question: {guiding_q}"
    if first_user:
        content += f"\n\nFirst user message: {first_user}"
    if last_ai:
        content += f"\n\nLast AI response: {last_ai}"

    response = client.messages.create(
        model=HUGINN_MODEL,
        max_tokens=80,
        system=[
            {
                "type": "text",
                "text": _SUMMARY_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": content}],
    )
    return response.content[0].text.strip()


def _fallback_summary(shard: dict) -> str:
    guiding_q = shard.get("guiding_question", "")
    return guiding_q[:200] if guiding_q else "(no summary available)"


def backfill(dry_run: bool = False, skip_summaries: bool = False) -> None:
    shard_dir = Path(SHARD_DIR)
    files = sorted(shard_dir.glob("*.json"))

    total = len(files)
    source_updated = 0
    summary_updated = 0
    skipped = 0
    errors = []

    for i, fpath in enumerate(files, 1):
        shard_id = fpath.stem
        try:
            shard, filepath = load_shard(shard_id)
        except Exception as exc:
            errors.append(f"{shard_id}: load error — {exc}")
            continue

        meta = shard.setdefault("meta_tags", {})
        changed = False

        # ── Source ──────────────────────────────────────────────────────────
        new_source = _infer_source(shard)
        if meta.get("source") != new_source:
            print(f"[{i}/{total}] {shard_id}: source {meta.get('source')!r} -> {new_source!r}")
            meta["source"] = new_source
            changed = True
            source_updated += 1
        else:
            skipped += 1

        # ── Summary ─────────────────────────────────────────────────────────
        if not skip_summaries and not meta.get("summary"):
            if CLAUDE_API_KEY:
                try:
                    summary = _summarise_via_haiku(shard)
                except Exception as exc:
                    summary = _fallback_summary(shard)
                    errors.append(f"{shard_id}: haiku error ({exc}), used fallback")
            else:
                summary = _fallback_summary(shard)
            print(f"[{i}/{total}] {shard_id}: summary -> {summary[:80]!r}")
            meta["summary"] = summary
            changed = True
            summary_updated += 1

        if changed and not dry_run:
            try:
                save_shard(filepath, shard)
            except Exception as exc:
                errors.append(f"{shard_id}: save error — {exc}")

    print(f"\n{'DRY RUN — ' if dry_run else ''}Done.")
    print(f"  Total shards:      {total}")
    print(f"  Source updated:    {source_updated}")
    print(f"  Summary generated: {summary_updated}")
    print(f"  Already correct:   {skipped}")
    if errors:
        print(f"  Errors ({len(errors)}):")
        for e in errors:
            print(f"    {e}")

    if not dry_run:
        print("\nRebuilding shard index...")
        update_index()
        print("Index rebuilt.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill source and summary fields on all NOVA shards.")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing.")
    parser.add_argument("--skip-summaries", action="store_true", help="Only assign source; skip summary generation.")
    args = parser.parse_args()
    backfill(dry_run=args.dry_run, skip_summaries=args.skip_summaries)
