"""
evolve.py — NOVA's self-evolution loop.

Inspired by Bernstein's evolve_mode.py (Donors/new/bernstein/).
When triggered, NOVA analyzes its own health, verifies tests, auto-commits
passing changes, adjusts focus weights, then produces a DIRECTOR REPORT shard
with 3-5 improvement tasks for the next Forgemaster sprint.

Key differences from Bernstein:
  - No HTTP task server — improvement tasks become NOVA shards
  - No worker agents spawned here — the current Claude session acts on the report
  - Scheduler is not deterministic Python — evolve is triggered manually or by NÓTT
  - Focus areas are NOVA-specific (shard health, nidhogg pipeline, forgemaster skills...)

Pipeline per cycle:
  1. ANALYZE  — shard health: confidence, stale, merge backlog, nidhogg annotations
  2. VERIFY   — run pytest if tests/ exists
  3. COMMIT   — git add → test → commit → push; rollback on failure
               if mcp/ changed → write restart_requested flag
  4. GOVERN   — AdaptiveGovernor adjusts focus area weights
  5. PLAN     — build PRODUCT DIRECTOR prompt, return as tool output
  6. LOG      — append to evolve_cycles.jsonl

Registration:
    register_evolve_tools(mcp, ctx)  — called once in nova_server.py

MCP Tools (1):
    nova_evolve — run one evolve cycle (dry_run supported)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, TYPE_CHECKING

from filelock import FileLock
from pydantic import BaseModel, Field, ConfigDict

from atomic_io import atomic_write_json
from config import SHARD_DIR, USAGE_LOG_FILE, MERGE_SIMILARITY_THRESHOLD
from gate_helpers import gate_check, log_executed
from permissions import is_blocked, denial_payload
from store import load_index, load_shard
from tool_registry import nova_tool

if TYPE_CHECKING:
    from server_context import ServerContext

# ── Paths ─────────────────────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).parent.parent
_EVOLVE_CONFIG_FILE  = _REPO_ROOT / "evolve.json"
_EVOLVE_CYCLES_FILE  = _REPO_ROOT / "evolve_cycles.jsonl"
_RESTART_FLAG        = _REPO_ROOT / ".sdd" / "runtime" / "restart_requested"
_TESTS_DIR           = _REPO_ROOT / "tests"

# ── Focus area rotation (NOVA-specific) ───────────────────────────────────────
_FOCUS_AREAS = [
    "shard_health",        # confidence decay, merge candidates, compaction backlog
    "nidhogg_pipeline",    # ingestion improvements, new source types, provenance
    "forgemaster_skills",  # skill library gaps, agent definitions, routing
    "nova_server",         # new MCP tools, performance, API surface
    "test_coverage",       # missing tests, edge cases
    "documentation",       # CLAUDE.md, SKILL.md, inline docstrings
    "routing_calibration", # HUGINN threshold drift, Forgemaster model routing accuracy
]

_FOCUS_INSTRUCTIONS = {
    "shard_health": (
        "Focus on shard quality: find shards with low confidence that should be "
        "archived, merge candidates that have been waiting, and compaction opportunities. "
        "Propose concrete maintenance tasks."
    ),
    "nidhogg_pipeline": (
        "Focus on the Nidhogg ingestion pipeline (mcp/nidhogg.py). "
        "What source types are missing? Is the Haiku analysis prompt optimal? "
        "Should the similarity threshold be tuned? Any provenance gaps?"
    ),
    "forgemaster_skills": (
        "Focus on the Forgemaster skill library and agent definitions. "
        "What domains are underserved? Which skills need updating? "
        "Are there routing decisions that could be smarter?"
    ),
    "nova_server": (
        "Focus on nova_server.py and the MCP tool surface. "
        "What operations are missing or clunky? Any performance issues? "
        "Are there tools that should be split or merged?"
    ),
    "test_coverage": (
        "Focus on test gaps in tests/. Which modules have no tests? "
        "What edge cases are unhandled? Prioritize store.py, maintenance.py, nidhogg.py."
    ),
    "documentation": (
        "Focus on documentation gaps: CLAUDE.md accuracy, mcp/SKILL.md completeness, "
        "missing docstrings in evolve.py and nidhogg.py. "
        "Is the onboarding flow still correct?"
    ),
    "routing_calibration": (
        "Focus on routing and retrieval calibration. Run nova_calibrate_routing to get "
        "the current HUGINN consistency analysis and Forgemaster routing pass rates. "
        "Propose: (1) the HUGINN_CONFIDENCE_THRESHOLD change to apply in .env if "
        "threshold_delta > 0.05, (2) any task_type→model routing corrections based on "
        "observed pass rates, (3) whether _COMPLEX_KEYWORDS in forgemaster_runtime.py "
        "should be expanded or narrowed based on empirical evidence. "
        "Ground all recommendations in the nova_calibrate_routing output, not intuition."
    ),
}


# ═══════════════════════════════════════════════════════════
# INPUT SCHEMA
# ═══════════════════════════════════════════════════════════

class NovaEvolveInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    dry_run: bool = Field(
        default=False,
        description="Analyze and plan without committing or writing cycle log.",
    )
    force: bool = Field(
        default=False,
        description="Skip cycle/budget limits and run regardless.",
    )


# ═══════════════════════════════════════════════════════════
# EVOLVE CONFIG
# ═══════════════════════════════════════════════════════════

def _load_evolve_config() -> dict[str, Any]:
    """Load evolve.json from repo root. Returns defaults if absent."""
    defaults: dict[str, Any] = {
        "enabled": True,
        "max_cycles": 0,       # 0 = unlimited
        "budget_usd": 0,       # 0 = no cap
        "interval_s": 300,     # minimum seconds between cycles
        "_cycle_count": 0,
        "_last_cycle_ts": 0.0,
        "_consecutive_empty": 0,
        "_spent_usd": 0.0,
        "_focus_weights": {area: 1.0 for area in _FOCUS_AREAS},
    }
    if not _EVOLVE_CONFIG_FILE.exists():
        return defaults
    try:
        with open(_EVOLVE_CONFIG_FILE, "r", encoding="utf-8") as f:
            stored = json.load(f)
        defaults.update(stored)
        return defaults
    except Exception:
        return defaults


def _save_evolve_config(cfg: dict[str, Any]) -> None:
    with FileLock(str(_EVOLVE_CONFIG_FILE) + ".lock", timeout=5):
        atomic_write_json(_EVOLVE_CONFIG_FILE, cfg)


def _limits_reached(cfg: dict[str, Any]) -> bool:
    max_cycles = cfg.get("max_cycles", 0)
    if max_cycles > 0 and cfg.get("_cycle_count", 0) >= max_cycles:
        return True
    budget = cfg.get("budget_usd", 0)
    if budget > 0 and cfg.get("_spent_usd", 0.0) >= budget:
        return True
    return False


# ═══════════════════════════════════════════════════════════
# SHARD HEALTH ANALYSIS
# ═══════════════════════════════════════════════════════════

@dataclass
class ShardHealthReport:
    total: int = 0
    low_confidence: int = 0
    stale: int = 0
    merge_candidates: int = 0
    nidhogg_annotated: int = 0
    nidhogg_merge_pending: int = 0
    avg_confidence: float = 1.0
    enriched: int = 0

    def summary(self) -> str:
        return (
            f"{self.total} shards total | "
            f"avg confidence {self.avg_confidence:.2f} | "
            f"{self.low_confidence} low-confidence | "
            f"{self.stale} stale | "
            f"{self.merge_candidates} merge candidates | "
            f"{self.nidhogg_annotated} nidhogg-annotated "
            f"({self.nidhogg_merge_pending} pending merge)"
        )


def _shard_health() -> ShardHealthReport:
    """Scan the shard index for health metrics. Read-only, no mutations."""
    index = load_index()
    report = ShardHealthReport(total=len(index))
    confidences = []

    for shard_id, entry in index.items():
        tags = entry.get("tags", [])
        if "forgotten" in tags or "archived" in tags:
            continue

        conf = entry.get("confidence", 1.0)
        confidences.append(conf)

        if "low_confidence" in tags:
            report.low_confidence += 1
        if "stale" in tags:
            report.stale += 1
        if "enriched" in tags:
            report.enriched += 1

        # Check nidhogg blocks and merge candidates in shard files
        shard_path = os.path.join(SHARD_DIR, shard_id + ".json")
        if not os.path.exists(shard_path):
            continue
        try:
            with open(shard_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            nidhogg_blocks = data.get("nidhogg", [])
            if nidhogg_blocks:
                report.nidhogg_annotated += 1
                if any(b.get("merge_candidate") for b in nidhogg_blocks):
                    report.nidhogg_merge_pending += 1
        except Exception:
            continue

    # Merge candidates from shard embeddings (approximate — count enriched pairs)
    # Exact merge detection is NÓTT's job; we just surface the enriched count as a proxy
    report.merge_candidates = max(0, report.enriched // 5)  # rough heuristic
    report.avg_confidence = round(sum(confidences) / len(confidences), 4) if confidences else 1.0
    return report


def _routing_health() -> dict:
    """
    Read calibration signals from forgemaster and HUGINN logs.

    Returns: huginn_consistency_rate, routing_success_by_model, suggested_threshold_delta,
    sample_size. Safe fallback dict on any import/IO error so evolve cycle continues.
    """
    try:
        from calibrate import (
            _load_huginn_log_sample,
            _compute_huginn_consistency,
            _suggest_threshold,
            _load_forgemaster_log,
            _compute_forgemaster_stats,
        )
        from config import HUGINN_CONFIDENCE_THRESHOLD, USAGE_LOG_FILE
    except ImportError:
        return {
            "huginn_consistency_rate": None,
            "routing_success_by_model": {},
            "suggested_threshold_delta": 0.0,
            "sample_size": 0,
            "note": "calibrate module not available",
        }

    try:
        import os
        from collections import defaultdict

        sample = _load_huginn_log_sample(USAGE_LOG_FILE, n=200)
        bucket_stats = _compute_huginn_consistency(sample, k_runs=5)
        suggested = _suggest_threshold(bucket_stats)
        delta = round(suggested - HUGINN_CONFIDENCE_THRESHOLD, 3)

        event_log = os.environ.get("FORGEMASTER_EVENT_LOG", "")
        forgemaster_events = _load_forgemaster_log(event_log)
        fm_stats = _compute_forgemaster_stats(forgemaster_events)

        # Aggregate pass rate per model (collapse task_type dimension)
        model_totals: dict = defaultdict(lambda: {"pass": 0, "total": 0})
        for key, counts in fm_stats.items():
            model = key.split(":", 1)[1] if ":" in key else key
            model_totals[model]["pass"] += counts["pass"]
            model_totals[model]["total"] += counts["total"]
        routing_success = {
            model: round(v["pass"] / max(v["total"], 1), 3)
            for model, v in model_totals.items()
            if v["total"] >= 5
        }

        best_consistency = max(
            (s["consistency_rate"] for s in bucket_stats.values() if s["total"] > 0),
            default=0.0,
        )

        return {
            "huginn_consistency_rate": round(best_consistency, 3),
            "routing_success_by_model": routing_success,
            "suggested_threshold_delta": delta,
            "sample_size": len(sample),
        }
    except Exception as exc:
        return {
            "huginn_consistency_rate": None,
            "routing_success_by_model": {},
            "suggested_threshold_delta": 0.0,
            "sample_size": 0,
            "note": f"calibration error: {exc}",
        }


# ═══════════════════════════════════════════════════════════
# TEST RUNNER
# ═══════════════════════════════════════════════════════════

@dataclass
class TestResult:
    passed: int = 0
    failed: int = 0
    summary: str = "no tests found"
    ran: bool = False


def _run_tests() -> TestResult:
    """Run pytest if tests/ exists. Returns structured result."""
    result = TestResult()
    if not _TESTS_DIR.exists():
        return result

    result.ran = True
    try:
        proc = subprocess.run(
            ["python", "-m", "pytest", str(_TESTS_DIR), "-x", "-q", "--tb=line"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(_REPO_ROOT),
            timeout=120,
        )
        output = proc.stdout + proc.stderr
        result.summary = output.strip().splitlines()[-1] if output.strip() else ""
        m = re.search(r"(\d+)\s+passed", output)
        if m:
            result.passed = int(m.group(1))
        m = re.search(r"(\d+)\s+failed", output)
        if m:
            result.failed = int(m.group(1))
    except subprocess.TimeoutExpired:
        result.summary = "pytest timed out after 120s"
    except FileNotFoundError:
        result.summary = "pytest not found — skipped"
        result.ran = False
    return result


# ═══════════════════════════════════════════════════════════
# AUTO-COMMIT
# ═══════════════════════════════════════════════════════════

@dataclass
class CommitResult:
    committed: bool = False
    restart_requested: bool = False
    reason: str = ""


_DEFAULT_COMMIT_ROOTS = ("mcp/", "forgemaster/", "tests/", "docs/")


def _allowed_commit_roots() -> tuple[str, ...]:
    """
    Positive allowlist of path prefixes ``_auto_commit`` may stage.
    Defaults to NOVA's source + skills + tests + docs. Override via
    ``EVOLVE_COMMIT_ROOTS`` (comma-separated).
    """
    raw = os.environ.get("EVOLVE_COMMIT_ROOTS", "")
    roots = [r.strip() for r in raw.split(",") if r.strip()] or list(_DEFAULT_COMMIT_ROOTS)
    return tuple(r if r.endswith("/") else r + "/" for r in roots)


def _auto_commit(dry_run: bool = False) -> CommitResult:
    """
    Stage allowlisted paths, verify tests pass, commit, push.
    If mcp/ source changed → write restart_requested flag.
    On test failure: rolls back via ``git stash push`` (recoverable, not destructive).
    """
    result = CommitResult()

    try:
        # Check for uncommitted changes
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=str(_REPO_ROOT),
        )
        allowed_roots = _allowed_commit_roots()
        changed_files = [
            line[3:].strip()
            for line in status.stdout.splitlines()
            if line.strip()
            and any(line[3:].strip().startswith(root) for root in allowed_roots)
        ]

        if not changed_files:
            result.reason = "no changes to commit (none under allowlist)"
            return result

        if dry_run:
            result.reason = f"dry_run: would commit {len(changed_files)} file(s)"
            return result

        # Run tests before committing
        test_result = _run_tests()
        if test_result.ran and test_result.failed > 0:
            # Stash the changes (recoverable) instead of `git checkout --`
            # which would silently destroy any parallel edits the user made.
            stash_label = f"evolve auto-rollback {datetime.now(UTC).isoformat(timespec='seconds')}"
            subprocess.run(
                ["git", "stash", "push", "-m", stash_label, "--"] + changed_files,
                cwd=str(_REPO_ROOT),
                capture_output=True,
            )
            result.reason = (
                f"tests failed ({test_result.failed} failures) — "
                f"changes stashed as: {stash_label!r} (recover with `git stash list`)"
            )
            return result

        # Stage and commit
        subprocess.run(
            ["git", "add", "--"] + changed_files,
            cwd=str(_REPO_ROOT),
            capture_output=True,
        )
        commit_msg = _build_commit_message(changed_files)
        commit = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            capture_output=True, text=True, cwd=str(_REPO_ROOT),
        )

        if commit.returncode != 0:
            result.reason = f"commit failed: {commit.stderr.strip()}"
            return result

        result.committed = True
        result.reason = f"committed {len(changed_files)} file(s)"

        # Restart signal if NOVA's own source changed
        mcp_changed = any(f.startswith("mcp/") for f in changed_files)
        if mcp_changed:
            _RESTART_FLAG.parent.mkdir(parents=True, exist_ok=True)
            _RESTART_FLAG.write_text(str(time.time()))
            result.restart_requested = True

    except Exception as e:
        result.reason = f"commit error: {e}"

    return result


def _build_commit_message(files: list[str]) -> str:
    """Build a short conventional commit message from changed files."""
    label_rules = [
        ("mcp/nidhogg", "feat(nidhogg): "),
        ("mcp/evolve", "feat(evolve): "),
        ("mcp/nott", "fix(nott): "),
        ("mcp/nova_server", "fix(server): "),
        ("mcp/store", "fix(store): "),
        ("mcp/maintenance", "fix(maintenance): "),
        ("mcp/", "fix(mcp): "),
        ("forgemaster/skills", "feat(skills): "),
        ("forgemaster/agents", "feat(agents): "),
        ("forgemaster/", "feat(forgemaster): "),
        ("tests/", "test: "),
        ("docs/", "docs: "),
        ("CLAUDE.md", "docs: "),
    ]
    seen: set[str] = set()
    labels: list[str] = []
    for f in files:
        for prefix, label in label_rules:
            if prefix in f and label not in seen:
                seen.add(label)
                labels.append(label.rstrip(": "))
                break

    area = ", ".join(labels[:3]) if labels else "update"
    return f"Evolve: {area} [{len(files)} file(s)]"


# ═══════════════════════════════════════════════════════════
# ADAPTIVE GOVERNOR
# ═══════════════════════════════════════════════════════════

def _adjust_weights(
    weights: dict[str, float],
    health: ShardHealthReport,
    tests: TestResult,
    consecutive_empty: int,
    routing: dict | None = None,
) -> tuple[dict[str, float], str]:
    """
    Nudge focus area weights based on current health signals.
    Returns updated weights and a reason string.
    """
    w = dict(weights)
    reasons = []

    # Ensure routing_calibration is present if it's a known focus area
    if "routing_calibration" not in w:
        w["routing_calibration"] = 1.0

    # Boost shard_health if confidence is degrading or merge backlog growing
    if health.avg_confidence < 0.7 or health.nidhogg_merge_pending > 3:
        w["shard_health"] = min(w.get("shard_health", 1.0) * 1.3, 3.0)
        reasons.append("low confidence / merge backlog")

    # Boost test_coverage if tests are failing or missing
    if tests.ran and tests.failed > 0:
        w["test_coverage"] = min(w.get("test_coverage", 1.0) * 1.5, 3.0)
        reasons.append("test failures detected")
    elif not tests.ran:
        w["test_coverage"] = min(w.get("test_coverage", 1.0) * 1.2, 3.0)
        reasons.append("no tests found")

    # Boost nidhogg if ingestion backlog growing
    if health.nidhogg_merge_pending > 5:
        w["nidhogg_pipeline"] = min(w.get("nidhogg_pipeline", 1.0) * 1.2, 3.0)
        reasons.append("nidhogg merge backlog")

    # Boost routing_calibration if HUGINN threshold has drifted significantly
    if routing and abs(routing.get("suggested_threshold_delta", 0.0)) > 0.05:
        w["routing_calibration"] = min(w.get("routing_calibration", 1.0) * 1.4, 3.0)
        delta = routing["suggested_threshold_delta"]
        reasons.append(f"routing threshold drift={delta:+.2f}")

    # Decay weights that haven't been acted on (diminishing returns)
    if consecutive_empty >= 2:
        for area in w:
            w[area] = max(0.5, w[area] * 0.9)
        reasons.append("diminishing returns decay")

    # Normalize so highest weight is always 1.0+
    max_w = max(w.values()) if w else 1.0
    if max_w > 1.0:
        w = {k: v / max_w for k, v in w.items()}

    reason = "; ".join(reasons) if reasons else "no adjustment needed"
    return w, reason


def _pick_focus(weights: dict[str, float], cycle_count: int) -> str:
    """Pick focus area: weighted random with cycle-count modulo as tiebreaker."""
    # Sort by weight descending, use cycle_count to break ties and ensure rotation
    sorted_areas = sorted(weights.items(), key=lambda x: (-x[1], x[0]))
    # Among top 2 by weight, rotate based on cycle count
    top2 = sorted_areas[:2]
    return top2[cycle_count % len(top2)][0]


# ═══════════════════════════════════════════════════════════
# DIRECTOR PROMPT
# ═══════════════════════════════════════════════════════════

def _build_director_prompt(
    cycle_number: int,
    focus_area: str,
    health: ShardHealthReport,
    tests: TestResult,
    commit: CommitResult,
    routing: dict | None = None,
) -> str:
    """Build the PRODUCT DIRECTOR prompt for the Forgemaster sprint."""
    focus_text = _FOCUS_INSTRUCTIONS.get(focus_area, "Focus on high-impact improvements.")

    routing_section = ""
    if routing and routing.get("huginn_consistency_rate") is not None:
        routing_section = (
            f"\n## Routing & Retrieval Health\n"
            f"HUGINN consistency rate: {routing['huginn_consistency_rate']:.1%} | "
            f"Suggested threshold delta: {routing['suggested_threshold_delta']:+.3f} | "
            f"Sample size: {routing['sample_size']}\n"
            f"Routing success by model: {routing.get('routing_success_by_model', {})}\n"
        )

    return f"""You are NOVA's PRODUCT DIRECTOR in EVOLVE mode (cycle {cycle_number}).

## System Health
{health.summary()}

## Test State
{"Passed: " + str(tests.passed) + " | Failed: " + str(tests.failed) + " | " + tests.summary if tests.ran else "No tests found."}

## Last Commit
{commit.reason}{"  ⚠ RESTART REQUESTED — mcp/ source changed" if commit.restart_requested else ""}
{routing_section}
## This Cycle's Focus: {focus_area.replace("_", " ").title()}
{focus_text}

## Your Task
Analyze NOVA's codebase and identify 3-5 high-impact improvements.
For each improvement, create a nova_shard with:
  - guiding_question: the specific problem being solved
  - intent: "planning"
  - theme: the relevant module (nidhogg / evolve / forgemaster / nova_server / tests / docs)

## Rules
- NEVER create tasks that are cosmetic or busy-work
- Each task must have a measurable outcome (test passes, bug fixed, tool added)
- Prefer targeted changes over architectural rewrites
- Create 3-5 shards MAX — quality over quantity
- Route implementation: Haiku (research/docs) | Gemini Flash (implementation) | Sonnet (architecture)
- DO NOT implement changes yourself in this step — only create planning shards

## Process
1. Read the relevant mcp/ files for the focus area
2. Check shard health metrics above
3. Identify the 3-5 highest-impact improvements
4. Create one nova_shard per improvement
5. Wire shards with nova_graph_relate if they depend on each other
6. Return a brief summary of what was planned
"""


# ═══════════════════════════════════════════════════════════
# CYCLE LOGGER
# ═══════════════════════════════════════════════════════════

def _log_cycle(
    cycle_number: int,
    focus_area: str,
    health: ShardHealthReport,
    tests: TestResult,
    commit: CommitResult,
    weight_reason: str,
    duration_s: float,
    routing: dict | None = None,
) -> None:
    entry = {
        "cycle": cycle_number,
        "iso_time": datetime.now(UTC).isoformat(),
        "focus_area": focus_area,
        "health": {
            "total_shards": health.total,
            "avg_confidence": health.avg_confidence,
            "low_confidence": health.low_confidence,
            "stale": health.stale,
            "nidhogg_merge_pending": health.nidhogg_merge_pending,
        },
        "tests": {
            "passed": tests.passed,
            "failed": tests.failed,
            "ran": tests.ran,
        },
        "commit": {
            "committed": commit.committed,
            "restart_requested": commit.restart_requested,
            "reason": commit.reason,
        },
        "weight_reason": weight_reason,
        "duration_s": round(duration_s, 2),
        "routing": {
            "huginn_consistency_rate": routing.get("huginn_consistency_rate") if routing else None,
            "suggested_threshold_delta": routing.get("suggested_threshold_delta", 0.0) if routing else 0.0,
        },
    }
    try:
        with open(_EVOLVE_CYCLES_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════
# MAIN CYCLE
# ═══════════════════════════════════════════════════════════

def run_evolve_cycle(dry_run: bool = False, force: bool = False) -> dict[str, Any]:
    """
    Run one evolve cycle. Returns a result dict with the director prompt
    and all cycle metrics for the calling tool handler to return.
    """
    t_start = time.monotonic()
    cfg = _load_evolve_config()

    if not force:
        if not cfg.get("enabled", True):
            return {"status": "disabled", "message": "Evolve mode is disabled in evolve.json"}

        if _limits_reached(cfg):
            return {"status": "limit_reached", "message": "Cycle or budget limit reached."}

        # Minimum interval between cycles
        last_ts = cfg.get("_last_cycle_ts", 0.0)
        interval = cfg.get("interval_s", 300)
        elapsed = time.time() - last_ts
        if elapsed < interval:
            wait = int(interval - elapsed)
            return {
                "status": "too_soon",
                "message": f"Last cycle was {int(elapsed)}s ago. Wait {wait}s or use force=True.",
            }

    cycle_number = cfg.get("_cycle_count", 0) + 1
    consecutive_empty = cfg.get("_consecutive_empty", 0)
    weights = cfg.get("_focus_weights", {area: 1.0 for area in _FOCUS_AREAS})

    # 1. ANALYZE
    health = _shard_health()
    routing = _routing_health()

    # 2. VERIFY
    tests = _run_tests()

    # 3. COMMIT
    commit = _auto_commit(dry_run=dry_run)

    # 4. GOVERN
    weights, weight_reason = _adjust_weights(weights, health, tests, consecutive_empty, routing=routing)
    focus_area = _pick_focus(weights, cycle_number)

    # 5. PLAN
    director_prompt = _build_director_prompt(cycle_number, focus_area, health, tests, commit, routing=routing)

    # Track diminishing returns
    produced = commit.committed
    new_consecutive_empty = 0 if produced else consecutive_empty + 1

    # 6. LOG
    duration_s = time.monotonic() - t_start
    if not dry_run:
        _log_cycle(cycle_number, focus_area, health, tests, commit, weight_reason, duration_s, routing=routing)
        cfg["_cycle_count"] = cycle_number
        cfg["_last_cycle_ts"] = time.time()
        cfg["_consecutive_empty"] = new_consecutive_empty
        cfg["_focus_weights"] = weights
        _save_evolve_config(cfg)

    return {
        "status": "ok",
        "cycle": cycle_number,
        "dry_run": dry_run,
        "focus_area": focus_area,
        "health": health.summary(),
        "tests": f"passed={tests.passed} failed={tests.failed} ran={tests.ran}",
        "commit": commit.reason,
        "committed": commit.committed,
        "restart_requested": commit.restart_requested,
        "weight_reason": weight_reason,
        "consecutive_empty": new_consecutive_empty,
        "duration_s": round(duration_s, 2),
        "director_prompt": director_prompt,
        "routing_health": routing,
    }


# ═══════════════════════════════════════════════════════════
# TOOL REGISTRATION
# ═══════════════════════════════════════════════════════════

def register_evolve_tools(mcp, ctx: "ServerContext") -> None:
    """Register the nova_evolve tool onto an existing MCPServer instance.
    Called once in nova_server.py — same pattern as Gemini and Nidhogg.
    """

    @nova_tool(mcp, name="nova_evolve")
    async def nova_evolve(params: NovaEvolveInput) -> str:
        """
        Run one NOVA self-evolution cycle.

        Analyzes shard health, verifies tests, auto-commits passing changes,
        adjusts focus area weights via AdaptiveGovernor, and returns a
        PRODUCT DIRECTOR prompt for the next Forgemaster sprint.

        Use the returned director_prompt to guide a nova_forgemaster_sprint
        or manual nova_shard_create calls to plan 3-5 improvements.

        dry_run=True: analyze and plan without committing or writing cycle log.
        force=True: skip interval/budget limits.
        """
        if is_blocked("nova_evolve"):
            return denial_payload("nova_evolve")

        # A dry run mutates nothing — skip the irreversible-write gate entirely.
        # A live cycle may git-commit allowlisted source, so pre-authorise the
        # irreversible capability before running and record the real outcome.
        request_id = None
        if not params.dry_run:
            gate_err, request_id = await gate_check(ctx, "nova_evolve", "evolve_auto_commit")
            if gate_err:
                return gate_err

        result = run_evolve_cycle(dry_run=params.dry_run, force=params.force)

        if not params.dry_run:
            log_executed(
                ctx, request_id, "nova_evolve",
                "evolve_auto_commit", ok=bool(result.get("committed")),
            )
        return json.dumps(result, indent=2)
