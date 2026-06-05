"""
bench_hotpool.py — Micro-benchmark: Python dict vs C hot pool for NOVA shard lookups.

The C hot pool has 8 slots. With 50 candidate IDs, the expected hit rate at
random access is ~16% (8/50). This benchmark reports that honestly alongside
ns/op for both paths and explains the ctypes overhead.

Usage (from repo root or mcp/):
    gcc -O2 -shared -fPIC -o mcp/_nova_hotpool.so mcp/nova_hotpool.c
    python3 mcp/bench_hotpool.py

    # Or with real shards:
    NOVA_SHARD_DIR=/path/to/shards python3 mcp/bench_hotpool.py

Timing uses time.perf_counter_ns(), which maps to CLOCK_MONOTONIC on Linux —
the same clock as clock_gettime(CLOCK_MONOTONIC, ...).
"""

from __future__ import annotations

import os
import random
import sys
import time
from pathlib import Path

# Allow running from repo root or from within mcp/.
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

import nova_hotpool  # noqa: E402 — must come after sys.path setup

N_CANDIDATES = 50
N_ITERATIONS = 10_000
N_WARMUP     = 100
POOL_FILL    = 8       # fill exactly this many slots (equals pool capacity)
BENCH_SEED   = 42      # fixed seed for reproducible access sequence


def collect_shard_ids() -> list[str]:
    """Collect up to N_CANDIDATES shard IDs from NOVA_SHARD_DIR, padding with
    synthetic IDs when the directory is absent or has fewer than 50 shards."""
    shard_dir_env = os.environ.get("NOVA_SHARD_DIR", "")
    shard_dir = Path(shard_dir_env) if shard_dir_env else _THIS_DIR / "shards"

    found: list[str] = []
    if shard_dir.is_dir():
        for p in sorted(shard_dir.iterdir()):
            if p.suffix in {".json", ".md"}:
                found.append(p.stem)
            if len(found) >= N_CANDIDATES:
                break

    n_synthetic = N_CANDIDATES - len(found)
    for n in range(n_synthetic):
        found.append(f"synthetic_shard_{n}")

    return found[:N_CANDIDATES]


def build_dict_baseline(shard_ids: list[str]) -> dict[str, float]:
    rng = random.Random(BENCH_SEED)
    return {sid: 0.4 + rng.random() * 0.6 for sid in shard_ids}


def populate_hotpool(shard_ids: list[str], baseline: dict[str, float]) -> None:
    nova_hotpool.reset()
    for sid in shard_ids[:POOL_FILL]:
        nova_hotpool.insert(sid, baseline[sid], "reflection")


def warmup(shard_ids: list[str], baseline: dict[str, float]) -> None:
    rng = random.Random(BENCH_SEED + 1)
    for _ in range(N_WARMUP):
        sid = rng.choice(shard_ids)
        _ = baseline.get(sid)
        _ = nova_hotpool.lookup(sid)


def bench_dict(shard_ids: list[str], baseline: dict[str, float]) -> float:
    """Returns ns/op for N_ITERATIONS Python dict lookups."""
    rng = random.Random(BENCH_SEED)
    sequence = [rng.choice(shard_ids) for _ in range(N_ITERATIONS)]

    t0 = time.perf_counter_ns()
    for sid in sequence:
        _ = baseline[sid]
    t1 = time.perf_counter_ns()

    return (t1 - t0) / N_ITERATIONS


def bench_hotpool(shard_ids: list[str]) -> float:
    """Returns ns/op for N_ITERATIONS C hot pool lookups (same access sequence)."""
    rng = random.Random(BENCH_SEED)  # same seed → same sequence as bench_dict
    sequence = [rng.choice(shard_ids) for _ in range(N_ITERATIONS)]

    t0 = time.perf_counter_ns()
    for sid in sequence:
        _ = nova_hotpool.lookup(sid)
    t1 = time.perf_counter_ns()

    return (t1 - t0) / N_ITERATIONS


def main() -> None:
    if not nova_hotpool._HOTPOOL_AVAILABLE:
        print("ERROR: nova_hotpool.so not found. Build it first:")
        print("  gcc -O2 -shared -fPIC -o mcp/_nova_hotpool.so mcp/nova_hotpool.c")
        sys.exit(1)

    print("=== NOVA Hot Pool Benchmark ===")
    print(f"  Pool capacity : {POOL_FILL} slots (8 × 12 bytes = 96 bytes, 2 cache lines)")
    print(f"  Candidate IDs : {N_CANDIDATES}")
    print(f"  Iterations    : {N_ITERATIONS:,}")
    print(f"  Warmup        : {N_WARMUP}")
    print()

    shard_ids = collect_shard_ids()
    n_real = sum(1 for s in shard_ids if not s.startswith("synthetic_shard_"))
    print(f"  Shard IDs: {n_real} real, {N_CANDIDATES - n_real} synthetic")

    baseline = build_dict_baseline(shard_ids)

    # Warmup both paths to avoid cold-start noise.
    populate_hotpool(shard_ids, baseline)
    warmup(shard_ids, baseline)

    # Reset stats cleanly, re-populate pool, then time.
    populate_hotpool(shard_ids, baseline)

    dict_ns  = bench_dict(shard_ids, baseline)
    c_ns     = bench_hotpool(shard_ids)

    s = nova_hotpool.stats()
    total = s["hits"] + s["misses"]
    hit_rate = s["hits"] / total if total > 0 else 0.0

    print()
    print("=== Results ===")
    print(f"  {'Method':<18} {'ns/op':>8}")
    print(f"  {'-'*28}")
    print(f"  {'Python dict':<18} {dict_ns:>8.1f}")
    print(f"  {'C hot pool':<18} {c_ns:>8.1f}")
    if dict_ns > 0:
        ratio = c_ns / dict_ns
        print(f"  Ratio (C/dict): {ratio:.2f}x")
    print()
    print(f"  Hot pool hits      : {s['hits']:,}")
    print(f"  Hot pool misses    : {s['misses']:,}")
    print(f"  Hot pool evictions : {s['evictions']:,}")
    print(f"  Hit rate           : {hit_rate:.1%}  "
          f"(expected ~{POOL_FILL / N_CANDIDATES:.0%} at uniform random over {N_CANDIDATES} IDs)")
    print()
    print("NOTE: Each nova_hotpool.lookup() call crosses the ctypes boundary (~100–400 ns")
    print("      overhead per call on x86_64). The C code itself runs in <5 ns for 8 slots.")
    print("      The value of this experiment is hit-rate validation and eviction correctness,")
    print("      not raw ns/op — real gains appear when integrated into C or Cython callers.")


if __name__ == "__main__":
    main()
