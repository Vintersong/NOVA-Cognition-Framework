"""
calibrate.py — Empirical routing and retrieval calibration for NOVA.

Implements the model-adaptive tool necessity methodology from arXiv:2605.14038:
  - HUGINN consistency analysis: measures how reliably the same query returns
    the same top-1 shard across repeated runs, per confidence bucket.
  - Forgemaster routing stats: computes sprint pass rates per (task_type, model)
    pair from sprint_verdict events emitted by forgemaster_runtime.

The tool only reads logs and reports findings. No thresholds are changed
automatically — apply suggested values via env vars (HUGINN_CONFIDENCE_THRESHOLD).
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict, deque
from pathlib import Path

from schemas import CalibrateRoutingInput

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).parent.parent

# Confidence buckets for HUGINN consistency analysis.
_CONFIDENCE_BUCKETS: list[tuple[float, float]] = [
    (0.5, 0.6),
    (0.6, 0.7),
    (0.7, 0.8),
    (0.8, 0.9),
    (0.9, 1.01),
]


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_huginn_log_sample(usage_log_file: str, n: int) -> list[dict]:
    """
    Read the last N HUGINN entries from nova_usage.jsonl.

    Streams the file line-by-line using a deque so only n lines are held in
    memory regardless of log file size. Returns [] on missing file or IO error.
    """
    path = Path(usage_log_file)
    if not path.exists():
        return []

    buffer: deque[str] = deque()
    try:
        with open(usage_log_file, "r", encoding="utf-8") as fh:
            for line in fh:
                if '"operator": "HUGINN"' in line:
                    buffer.append(line)
                    if len(buffer) > n:
                        buffer.popleft()
    except OSError as exc:
        logger.warning("calibrate: could not read usage log %s — %s", usage_log_file, exc)
        return []

    results: list[dict] = []
    for line in reversed(list(buffer)):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        results.append({
            "query_sha256_16": entry.get("metadata", {}).get("query_sha256_16", ""),
            "max_confidence":   entry.get("metadata", {}).get("max_confidence", 0.0),
            "shard_ids":        entry.get("shards", []),
            "used_llm":         entry.get("metadata", {}).get("used_llm", False),
        })

    return results


def _load_forgemaster_log(event_log_path: str) -> list[dict]:
    """
    Read sprint_verdict events from forgemaster JSONL logs.

    Checks FORGEMASTER_EVENT_LOG first; falls back to scanning all
    output/forgemaster_runs/*.jsonl files. Returns [] gracefully if none exist.
    """
    log_files: list[str] = []
    if event_log_path and os.path.exists(event_log_path):
        log_files = [event_log_path]
    else:
        run_dir = _REPO_ROOT / "output" / "forgemaster_runs"
        if run_dir.exists():
            log_files = [str(p) for p in sorted(run_dir.glob("*.jsonl"))]

    results: list[dict] = []
    for log_path in log_files:
        try:
            with open(log_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("role") == "outcome" and ev.get("event") == "sprint_verdict":
                        results.append({
                            "task_type": ev.get("task_type", ""),
                            "routed_model": ev.get("routed_model", ""),
                            "outcome": ev.get("outcome", ""),
                            "routing_confidence": ev.get("routing_confidence", 0.0),
                        })
        except OSError as exc:
            logger.warning("calibrate: could not read forgemaster log %s — %s", log_path, exc)

    return results


# ── Analysis ──────────────────────────────────────────────────────────────────

def _top1_consistent(entries: list[dict]) -> bool:
    """True if all entries in the group returned the same top-1 shard_id."""
    top1s = [e["shard_ids"][0] for e in entries if e.get("shard_ids")]
    return len(set(top1s)) <= 1 and bool(top1s)


def _compute_huginn_consistency(sample: list[dict], k_runs: int) -> dict[str, dict]:
    """
    Measure top-1 shard consistency per confidence bucket.

    Groups log entries by query_sha256_16. For entries that appear >= k_runs
    times, checks whether they all returned the same top-1 shard. Returns
    per-bucket stats: {bucket_label: {total, consistent, consistency_rate}}.
    """
    by_hash: dict[str, list[dict]] = defaultdict(list)
    for entry in sample:
        qhash = entry.get("query_sha256_16", "")
        if qhash:
            by_hash[qhash].append(entry)

    bucket_stats: dict[str, dict] = {}
    for lo, hi in _CONFIDENCE_BUCKETS:
        label = f"{lo:.1f}-{min(hi, 1.0):.1f}"
        in_bucket = [e for e in sample if lo <= e.get("max_confidence", 0.0) < hi]
        consistent = sum(
            1 for e in in_bucket
            if len(by_hash.get(e.get("query_sha256_16", ""), [])) >= k_runs
            and _top1_consistent(by_hash[e.get("query_sha256_16", "")])
        )
        bucket_stats[label] = {
            "total": len(in_bucket),
            "consistent": consistent,
            "consistency_rate": round(consistent / max(len(in_bucket), 1), 3),
        }

    return bucket_stats


def _suggest_threshold(bucket_stats: dict[str, dict], min_consistency: float = 0.80) -> float:
    """
    Return the lowest confidence value whose bucket hits >= min_consistency.

    Falls back to current HUGINN_CONFIDENCE_THRESHOLD if no bucket qualifies.
    """
    from config import HUGINN_CONFIDENCE_THRESHOLD
    for lo, hi in _CONFIDENCE_BUCKETS:
        label = f"{lo:.1f}-{min(hi, 1.0):.1f}"
        stats = bucket_stats.get(label, {})
        if stats.get("total", 0) > 0 and stats.get("consistency_rate", 0.0) >= min_consistency:
            return lo
    return HUGINN_CONFIDENCE_THRESHOLD


def _compute_replay_divergence(sample: list[dict], k_runs: int) -> dict:
    """
    Detect replay divergence in HUGINN retrieval.

    For every query that appears ``>= k_runs`` times, observe the top-1
    result of each run, where "result" is the top-1 shard_id string or
    ``None`` for a miss (no shard_ids returned). The query is **divergent**
    iff the set of observed results has more than one distinct element.

    Two semantic refinements over a naive "compare top-1 strings" version:

    - Queries whose every run is a miss are **excluded** from the
      denominator (cannot be divergent — there is no positive signal to
      compare). They are surfaced separately as ``queries_zero_hit`` so
      the operator can see what was filtered.
    - A "miss" is treated as a distinct result, not silently dropped. A
      run sequence of ``['s1'], [], ['s1']`` is divergent (the retrieval
      sometimes hits and sometimes doesn't), even though the hits agree.

    The rate is a proxy for the "replay divergence" failure mode from
    Srinivasan 2026 (arXiv 2605.20173, §5.1): the same input produces
    different downstream outputs across runtime conditions (model version,
    prompt revision, retrieval index update).

    For NOVA specifically the runtime conditions that drift are:
      - confidence decay across calendar time (NOVA_DECAY_RATE),
      - HUGINN model version changes,
      - new shards entering the corpus shifting top-1 ranking.

    Returns:
      {
        "queries_repeated":    int,   # queries with >= k_runs runs AND at least one hit
        "queries_zero_hit":    int,   # queries with >= k_runs runs but all misses (excluded)
        "queries_divergent":   int,   # subset of repeated whose result varied across runs
        "divergence_rate":     float, # divergent / repeated (0.0 if repeated == 0)
        "examples":            list,  # up to 5 divergent query hashes and their distinct results
        "note":                str,
      }
    """
    by_hash: dict[str, list[dict]] = defaultdict(list)
    for entry in sample:
        qhash = entry.get("query_sha256_16", "")
        if qhash:
            by_hash[qhash].append(entry)

    repeated = 0
    zero_hit = 0
    divergent = 0
    examples: list[dict] = []
    for qhash, entries in by_hash.items():
        if len(entries) < k_runs:
            continue

        # Each run contributes either its top-1 shard_id or None (miss).
        per_run_top1: list[str | None] = [
            (e["shard_ids"][0] if e.get("shard_ids") else None)
            for e in entries
        ]

        # All-miss queries cannot be divergent — exclude from the denominator
        # and report separately so the operator sees the filter.
        if all(r is None for r in per_run_top1):
            zero_hit += 1
            continue

        repeated += 1
        distinct_results = set(per_run_top1)
        if len(distinct_results) > 1:
            divergent += 1
            if len(examples) < 5:
                examples.append({
                    "query_sha256_16": qhash,
                    "runs": len(entries),
                    # Sorted with misses (None) first for stable JSON output.
                    "distinct_top1": sorted(
                        distinct_results, key=lambda r: (r is not None, r or "")
                    ),
                    "max_confidence": max(
                        (e.get("max_confidence", 0.0) for e in entries),
                        default=0.0,
                    ),
                })

    rate = round(divergent / max(repeated, 1), 3)
    if repeated == 0:
        note = (
            "No queries had >= k_runs runs with at least one hit — divergence "
            "cannot be measured. Increase sample_size, lower k_runs, or check "
            "queries_zero_hit if it is high (HUGINN may be missing the corpus)."
        )
    elif rate >= 0.20:
        note = (
            "HIGH replay divergence (>= 20%): retrieval is unstable across runs. "
            "Consider pinning HUGINN_MODEL, lowering NOVA_DECAY_RATE, or migrating "
            "the affected lookup path to a deterministic CAS-shaped store."
        )
    elif rate >= 0.05:
        note = (
            "Moderate replay divergence (5–20%). Track over time — a rising trend "
            "is the trigger to investigate spine choice for affected queries."
        )
    else:
        note = "Replay divergence below 5% — retrieval is stable for repeated queries."

    return {
        "queries_repeated": repeated,
        "queries_zero_hit": zero_hit,
        "queries_divergent": divergent,
        "divergence_rate": rate,
        "examples": examples,
        "note": note,
    }


def _compute_forgemaster_stats(events: list[dict]) -> dict[str, dict]:
    """
    Compute sprint pass rates grouped by "task_type:routed_model".

    Returns {key: {pass, fail, total, pass_rate}} for each group.
    """
    groups: dict[str, dict] = defaultdict(lambda: {"pass": 0, "fail": 0})
    for ev in events:
        key = f"{ev.get('task_type', 'unknown')}:{ev.get('routed_model', 'unknown')}"
        if ev.get("outcome") == "review_pass":
            groups[key]["pass"] += 1
        elif ev.get("outcome") == "review_fail":
            groups[key]["fail"] += 1

    result: dict[str, dict] = {}
    for key, counts in groups.items():
        total = counts["pass"] + counts["fail"]
        result[key] = {
            **counts,
            "total": total,
            "pass_rate": round(counts["pass"] / max(total, 1), 3),
        }
    return result


# ── Core calibration runner ───────────────────────────────────────────────────

def _run_calibration(params: CalibrateRoutingInput) -> dict:
    from config import HUGINN_CONFIDENCE_THRESHOLD, USAGE_LOG_FILE

    huginn_sample = _load_huginn_log_sample(USAGE_LOG_FILE, params.sample_size)
    bucket_stats = _compute_huginn_consistency(huginn_sample, params.k_runs)
    suggested_threshold = _suggest_threshold(bucket_stats)

    result: dict = {
        "huginn": {
            "current_threshold": HUGINN_CONFIDENCE_THRESHOLD,
            "suggested_threshold": suggested_threshold,
            "threshold_delta": round(suggested_threshold - HUGINN_CONFIDENCE_THRESHOLD, 3),
            "sample_size": len(huginn_sample),
            "bucket_stats": bucket_stats,
            "note": (
                "To apply: set HUGINN_CONFIDENCE_THRESHOLD=<suggested_threshold> in .env. "
                "No code change required."
            ),
        }
    }

    if params.include_replay_divergence:
        result["replay_divergence"] = _compute_replay_divergence(huginn_sample, params.k_runs)

    if params.include_forgemaster:
        event_log = os.environ.get("FORGEMASTER_EVENT_LOG", "")
        events = _load_forgemaster_log(event_log)
        forgemaster_stats = _compute_forgemaster_stats(events)
        result["forgemaster"] = {
            "total_outcome_events": len(events),
            "routing_stats": forgemaster_stats,
            "note": (
                "Routing stats are populated once sprint_verdict events exist "
                "(requires task_type to be passed to nova_forgemaster_sprint)."
            ),
        }

    return result


# ── Tool registration ─────────────────────────────────────────────────────────

def register_calibrate_tools(mcp) -> None:
    import asyncio
    from permissions import is_blocked, denial_payload
    from tool_registry import nova_tool

    @nova_tool(mcp, name="nova_calibrate_routing")
    async def nova_calibrate_routing(params: CalibrateRoutingInput) -> str:
        """
        Analyse HUGINN retrieval logs and Forgemaster sprint logs to calibrate
        routing thresholds and model routing based on empirical performance data.

        HUGINN calibration: measures top-1 shard consistency across repeated
        queries per confidence bucket. Returns the lowest threshold at which
        consistency >= 80% and a delta vs. the current HUGINN_CONFIDENCE_THRESHOLD.

        Forgemaster calibration: computes sprint pass rates per (task_type, model)
        pair from sprint_verdict events (requires task_type in nova_forgemaster_sprint).

        This tool is read-only — it reports findings only. Apply threshold changes
        via env var: HUGINN_CONFIDENCE_THRESHOLD=<value>.
        """
        if is_blocked("nova_calibrate_routing"):
            return denial_payload("nova_calibrate_routing")

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _run_calibration, params)
        return json.dumps(result, indent=2)
