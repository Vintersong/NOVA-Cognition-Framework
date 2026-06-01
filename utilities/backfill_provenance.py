"""
backfill_provenance.py — add an epistemic_provenance record to existing shards.

Shards created before the provenance feature have no
`meta_tags.epistemic_provenance` field. This one-shot migration backfills a
default record derived from each shard's existing `source` origin tag (see
provenance.default_provenance). It is idempotent: shards that already carry a
record are left untouched.

Usage:
  cd mcp && python ../utilities/backfill_provenance.py [--dry-run]

Flags:
  --dry-run   Print what would change, write nothing.
"""

import argparse
import sys
from pathlib import Path

# Allow importing from mcp/ when run from repo root or utilities/
_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT / "mcp"))

from dotenv import load_dotenv
load_dotenv(dotenv_path=_REPO_ROOT / ".env")

import provenance
from config import SHARD_DIR
from store import load_shard, mutate_shard, update_index


def _shard_ids() -> list[str]:
    """Collect shard ids from both .md and .json files (.md wins per id)."""
    shard_dir = Path(SHARD_DIR)
    ids: set[str] = set()
    for p in shard_dir.glob("*.md"):
        ids.add(p.stem)
    for p in shard_dir.glob("*.json"):
        if not p.name.endswith(".lock"):
            ids.add(p.stem)
    return sorted(ids)


def backfill(dry_run: bool = False) -> None:
    ids = _shard_ids()
    total = len(ids)
    added = 0
    skipped = 0
    errors: list[str] = []

    for i, shard_id in enumerate(ids, 1):
        try:
            shard, filepath = load_shard(shard_id)
        except Exception as exc:
            errors.append(f"{shard_id}: load error — {exc}")
            continue

        meta = shard.get("meta_tags", {})
        if isinstance(meta.get("epistemic_provenance"), dict):
            skipped += 1
            continue

        record = provenance.default_provenance(meta)
        print(f"[{i}/{total}] {shard_id}: + epistemic_provenance "
              f"(source_type={record['source_type']})")
        added += 1

        if not dry_run:
            try:
                # Re-derive under the lock so a concurrent write isn't clobbered.
                def _add(fresh: dict) -> None:
                    fmeta = fresh.setdefault("meta_tags", {})
                    if not isinstance(fmeta.get("epistemic_provenance"), dict):
                        fmeta["epistemic_provenance"] = provenance.default_provenance(fmeta)
                mutate_shard(shard_id, _add)
            except Exception as exc:
                errors.append(f"{shard_id}: save error — {exc}")

    print(f"\n{'DRY RUN — ' if dry_run else ''}Done.")
    print(f"  Total shards:   {total}")
    print(f"  Records added:  {added}")
    print(f"  Already had it: {skipped}")
    if errors:
        print(f"  Errors ({len(errors)}):")
        for e in errors:
            print(f"    {e}")

    if not dry_run and added:
        print("\nRebuilding shard index...")
        update_index()
        print("Index rebuilt.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill epistemic_provenance records on all NOVA shards."
    )
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing.")
    args = parser.parse_args()
    backfill(dry_run=args.dry_run)
