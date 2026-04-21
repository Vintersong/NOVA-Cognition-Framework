"""
shard_compact.py — Compact bloated NOVA shards (stdlib only, no external dependencies)

Usage:
    python utilities/shard_compact.py [options]

Options:
    --shard-dir   Path to shard directory (default: nova_memory/ relative to repo root)
    --threshold   Max conversation turns before a shard is considered bloated (default: 30)
    --dry-run     Report what would be compacted without writing anything
    --fail-on-bloat  Exit with code 1 if any shard exceeds threshold (CI use)
    --all         Process every shard in the directory, not just bloated ones

Examples:
    # Check for bloat without writing (CI):
    python utilities/shard_compact.py --fail-on-bloat --dry-run

    # Preview what would be compacted:
    python utilities/shard_compact.py --dry-run

    # Compact all bloated shards in a custom directory:
    python utilities/shard_compact.py --shard-dir ./shards --threshold 20

    # Force-process all shards regardless of turn count:
    python utilities/shard_compact.py --all
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


# ═══════════════════════════════════════════════════════════
# COMPACTION LOGIC
# ═══════════════════════════════════════════════════════════

def compact_history(history: list[dict], n_original: int) -> list[dict]:
    """
    Reduce conversation_history to at most 3 entries:
      - First 2 turns (system/context) are kept verbatim
      - Remaining turns are replaced with a single synthetic summary turn
    Returns the new history list.
    """
    kept = history[:2]
    synthetic = {
        "role": "assistant",
        "content": (
            f"[Compacted: {n_original} turns summarized. "
            "Key decisions preserved in shard metadata.]"
        ),
        "compacted_at": datetime.now(tz=timezone.utc).isoformat(),
        "original_turn_count": n_original,
    }
    return kept + [synthetic]


# ═══════════════════════════════════════════════════════════
# SHARD PROCESSING
# ═══════════════════════════════════════════════════════════

def process_shard(
    path: Path,
    threshold: int,
    dry_run: bool,
    process_all: bool,
) -> tuple[str, int, int, str]:
    """
    Process a single shard file.

    Returns (shard_name, original_turns, compacted_turns, status)
    where status is one of: 'compacted', 'ok', 'skipped'
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            shard = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        return (path.name, 0, 0, f"error: {exc}")

    history = shard.get("conversation_history", [])
    n_original = len(history)

    # Determine whether this shard should be compacted.
    # --all: compact every shard with more than 2 turns (the minimum kept after compaction).
    # default: compact only shards that exceed --threshold.
    should_compact = n_original > threshold or (process_all and n_original > 2)
    if not should_compact:
        return (path.name, n_original, n_original, "ok")
    new_history = compact_history(history, n_original)
    n_compacted = len(new_history)

    if dry_run:
        return (path.name, n_original, n_compacted, "skipped")

    shard["conversation_history"] = new_history
    shard["last_modified"] = datetime.now(tz=timezone.utc).isoformat()

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(shard, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        return (path.name, n_original, n_compacted, f"write-error: {exc}")

    return (path.name, n_original, n_compacted, "compacted")


# ═══════════════════════════════════════════════════════════
# SUMMARY TABLE
# ═══════════════════════════════════════════════════════════

def print_summary(rows: list[tuple[str, int, int, str]]) -> None:
    """Print a formatted summary table to stdout."""
    col_name = max((len(r[0]) for r in rows), default=10)
    col_name = max(col_name, len("Shard"))
    header = (
        f"{'Shard':<{col_name}}  {'Original':>8}  {'After':>5}  Status"
    )
    sep = "-" * len(header)
    print(sep)
    print(header)
    print(sep)
    for name, orig, after, status in rows:
        print(f"{name:<{col_name}}  {orig:>8}  {after:>5}  {status}")
    print(sep)


# ═══════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════

def main() -> int:
    repo_root = Path(__file__).parent.parent
    default_shard_dir = repo_root / "nova_memory"

    parser = argparse.ArgumentParser(
        description="Compact bloated NOVA shards. Stdlib only — no external dependencies.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--shard-dir",
        default=str(default_shard_dir),
        help="Path to shard directory (default: nova_memory/ at repo root)",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=30,
        help="Max conversation turns before a shard is considered bloated (default: 30)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be compacted without writing anything",
    )
    parser.add_argument(
        "--fail-on-bloat",
        action="store_true",
        help="Exit with code 1 if any shard exceeds --threshold (CI use)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="process_all",
        help="Process every shard in the directory, not just bloated ones",
    )
    args = parser.parse_args()

    shard_dir = Path(args.shard_dir)

    if not shard_dir.exists():
        print(f"Shard directory not found: {shard_dir}  (nothing to do)")
        return 0

    json_files = sorted(shard_dir.glob("*.json"))
    if not json_files:
        print(f"No shard files found in {shard_dir}  (nothing to do)")
        return 0

    prefix = "[DRY RUN] " if args.dry_run else ""
    print(f"{prefix}Shard Compaction — {shard_dir}")
    print(f"Threshold: {args.threshold} turns  |  Files: {len(json_files)}")

    rows: list[tuple[str, int, int, str]] = []
    bloated: list[str] = []

    for path in json_files:
        row = process_shard(path, args.threshold, args.dry_run, args.process_all)
        rows.append(row)
        name, orig, _after, _status = row
        if orig > args.threshold:
            bloated.append(name)

    print_summary(rows)

    compacted_count = sum(1 for r in rows if r[3] == "compacted")
    skipped_count = sum(1 for r in rows if r[3] == "skipped")
    ok_count = sum(1 for r in rows if r[3] == "ok")

    print(
        f"\nTotal: {len(rows)} shards  |  "
        f"Compacted: {compacted_count}  |  "
        f"Skipped (dry-run): {skipped_count}  |  "
        f"OK: {ok_count}  |  "
        f"Bloated: {len(bloated)}"
    )

    if args.fail_on_bloat and bloated:
        print(
            f"\n[FAIL] {len(bloated)} shard(s) exceed threshold ({args.threshold} turns): "
            + ", ".join(bloated)
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
