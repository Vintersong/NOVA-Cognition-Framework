"""
bench_report.py — Roll the retrieval benchmark JSONL into a text table.

Usage:
    python utilities/bench_report.py [path/to/bench_retrieval.jsonl]

Reads the JSONL emitted by tests/bench_retrieval.py + the MUNINN inner
timer in mcp/ravens.py, groups by (corpus_size, stage), prints a
per-corpus-size block with p50/p95 latency, funnel ratios, and recall.

stdlib only. No pandas, no plotting — eyeball-friendly columns.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

DEFAULT_PATH = "bench_retrieval.jsonl"

# Order in which stages are printed inside each corpus-size block.
STAGE_ORDER = [
    "facts_prefilter",
    "huginn_local",
    "huginn_llm",
    "muninn_local",
    "muninn_llm",
    "spreading_activation_inner",
    "cluster_collapse_offpath",
]


def _quantile(values: list[float], q: float) -> float:
    """p50 / p95. statistics.quantiles needs >=2 points; fall back to the
    single value otherwise."""
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    qs = statistics.quantiles(values, n=100, method="inclusive")
    # qs has 99 cutpoints (between 1% and 99%). Index q-1 = the q-th percentile.
    idx = max(0, min(98, int(q) - 1))
    return qs[idx]


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _fmt(v: float | None, width: int = 8, prec: int = 2) -> str:
    if v is None:
        return "-".rjust(width)
    return f"{v:>{width}.{prec}f}"


def main(jsonl_path: str = DEFAULT_PATH) -> int:
    path = Path(jsonl_path)
    if not path.exists():
        print(f"bench_report: file not found: {path}", file=sys.stderr)
        return 1

    rows: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"bench_report: bad JSON line skipped: {exc}", file=sys.stderr)

    if not rows:
        print("bench_report: no rows in file")
        return 1

    # Group: (corpus_size, stage) -> list[row]
    # spreading_activation_inner rows come from the MUNINN inner timer,
    # which doesn't carry corpus_size — they get bucketed under "inner".
    grouped: dict[tuple[object, str], list[dict]] = defaultdict(list)
    for r in rows:
        size = r.get("corpus_size", "inner")
        grouped[(size, r["stage"])].append(r)

    sizes = sorted(
        {size for (size, _) in grouped.keys() if isinstance(size, int)}
    )
    has_inner = any(size == "inner" for (size, _) in grouped.keys())

    header = (
        f"{'stage':<28} {'n':>4} {'p50_ms':>9} {'p95_ms':>9} "
        f"{'cand_in':>8} {'cand_out':>9} {'r@5':>6} {'r@10':>6} {'in_live':>8}"
    )
    sep = "-" * len(header)

    print(f"\nretrieval bench report — {path}\n")

    for size in sizes:
        print(f"corpus_size = {size}")
        print(sep)
        print(header)
        print(sep)
        for stage in STAGE_ORDER:
            bucket = grouped.get((size, stage), [])
            if not bucket:
                continue
            _print_row(stage, bucket)
        print()

    if has_inner:
        print("spreading_activation (MUNINN inner timer — no corpus_size context)")
        print(sep)
        print(header)
        print(sep)
        for stage in STAGE_ORDER:
            bucket = grouped.get(("inner", stage), [])
            if not bucket:
                continue
            _print_row(stage, bucket)
        print()

    return 0


def _print_row(stage: str, bucket: list[dict]) -> None:
    durations = [r["duration_ms"] for r in bucket if r.get("duration_ms") is not None]
    cand_in = [r["candidates_in"] for r in bucket if r.get("candidates_in") is not None]
    cand_out = [r["candidates_out"] for r in bucket if r.get("candidates_out") is not None]
    r5 = [r["recall_at_5"] for r in bucket if r.get("recall_at_5") is not None]
    r10 = [r["recall_at_10"] for r in bucket if r.get("recall_at_10") is not None]
    in_live = any(r.get("in_live_pipeline") for r in bucket)

    print(
        f"{stage:<28} {len(bucket):>4} "
        f"{_fmt(_quantile(durations, 50), 9, 2)} "
        f"{_fmt(_quantile(durations, 95), 9, 2)} "
        f"{_fmt(_mean(cand_in), 8, 1)} "
        f"{_fmt(_mean(cand_out), 9, 1)} "
        f"{_fmt(_mean(r5) if r5 else None, 6, 3)} "
        f"{_fmt(_mean(r10) if r10 else None, 6, 3)} "
        f"{'yes' if in_live else 'no':>8}"
    )


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    sys.exit(main(arg))
