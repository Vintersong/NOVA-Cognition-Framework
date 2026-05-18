"""
forgemaster_runtime.py — Forgemaster execution harness.

ForgemasterRuntime orchestrates the 4-turn sprint lifecycle:
  orchestrator → planner → implementer → reviewer

Uses NOVA as the shared memory backplane (via SessionStore) and respects
tool access boundaries via ToolPermissionContext.

Real model dispatch:
  - orchestrator / planner / reviewer → Anthropic (Sonnet by default)
  - implementer → Google GenAI (Gemini Flash by default)
  - provider is picked from the model name prefix (claude-* / gemini-*)

Per-call events are appended to the path in FORGEMASTER_EVENT_LOG
(environment variable) when set — one JSON object per line.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import (
    HUGINN_MODEL,
    MUNINN_MODEL,
    GEMINI_MODEL,
    SHARD_DIR,
    FORGEMASTER_ORCHESTRATOR_MODEL,
    FORGEMASTER_PLANNER_MODEL,
    FORGEMASTER_REVIEWER_MODEL,
    FORGEMASTER_IMPLEMENTER_MODEL,
)
from permissions import ToolPermissionContext
from session_store import SessionStore, NovaSession
from graph import add_corroborated_by
from skill_manifest import SkillManifest, parse_skill_manifest
from capability_gate import CapabilityGate, CapabilityDenied, HITLDenied

logger = logging.getLogger(__name__)

# Repo root — resolved relative to this file so it works regardless of CWD.
_REPO_ROOT = Path(__file__).parent.parent

# ─────────────────────────────────────────────────────────────────────────────
# Routing table — sourced from forgemaster/AGENTS.md preferred_models section
# ─────────────────────────────────────────────────────────────────────────────
_ROUTING_TABLE: dict[str, str] = {
    # claude-sonnet lane
    "architecture": MUNINN_MODEL,
    "review": MUNINN_MODEL,
    "ambiguity": MUNINN_MODEL,
    # gemini-flash lane
    "implementation": GEMINI_MODEL,
    "boilerplate": GEMINI_MODEL,
    "structured-output": GEMINI_MODEL,
    # claude-haiku lane
    "research": HUGINN_MODEL,
    "documentation": HUGINN_MODEL,
    "fast-tasks": HUGINN_MODEL,
    # claude design lane (Sonnet handles UI/frontend — stitch was a placeholder, now deprecated)
    "ui": MUNINN_MODEL,
    "frontend": MUNINN_MODEL,
    "mockup": MUNINN_MODEL,
}

# Agent roles that require write-capable tools.  If those tools are denied,
# the corresponding lanes are flagged as restricted.
_WRITE_DEPENDENT_LANES: frozenset[str] = frozenset({"implementer"})

# Canonical write tool names (a subset of _ALL_TOOL_NAMES in nova_server.py)
_WRITE_TOOLS: frozenset[str] = frozenset({
    "nova_shard_create",
    "nova_shard_update",
    "nova_shard_merge",
    "nova_shard_archive",
    "nova_shard_forget",
})

# Complexity override: task types containing these words are routed to claude-sonnet
# regardless of what the routing table says.
# Inspired by hermes-agent smart_model_routing.py choose_cheap_model_route().
_COMPLEX_KEYWORDS: frozenset[str] = frozenset({
    "debug", "debugging", "investigation", "analysis", "architecture",
    "migration", "refactor", "security", "performance", "integration",
    "system-design", "database", "authentication", "authorization",
    "tracing", "profiling", "concurrency",
})

# Empirical routing cache — loaded lazily from forgemaster run logs.
# Keyed by (task_type, model); values are {"pass": int, "fail": int}.
_empirical_routing_stats: dict[tuple[str, str], dict[str, int]] = {}
_empirical_stats_loaded: bool = False
_empirical_stats_loaded_at: float = 0.0
_EMPIRICAL_MIN_SAMPLES: int = 10  # minimum samples before empirical route overrides static rules
# Cache TTL — override via NOVA_EMPIRICAL_CACHE_TTL_S env var (default 5 minutes).
# A short TTL means long-running MCP servers pick up new sprint outcomes without restart.
_EMPIRICAL_CACHE_TTL_S: float = float(os.environ.get("NOVA_EMPIRICAL_CACHE_TTL_S", "300"))

# Role-to-model mapping for the 4-turn sprint pipeline. Each role resolves
# through its FORGEMASTER_*_MODEL env var (see config.py), which falls back
# to MUNINN_MODEL / GEMINI_MODEL when unset.
_ROLE_TO_MODEL: dict[str, str] = {
    "orchestrator": FORGEMASTER_ORCHESTRATOR_MODEL,
    "planner":      FORGEMASTER_PLANNER_MODEL,
    "implementer":  FORGEMASTER_IMPLEMENTER_MODEL,
    "reviewer":     FORGEMASTER_REVIEWER_MODEL,
}

# Optional event log — one JSONL line per LLM call.
_EVENT_LOG_PATH = os.environ.get("FORGEMASTER_EVENT_LOG", "")


def _log_event(entry: dict) -> None:
    """
    Append a single LLM-call event to a JSONL log.

    Path selection:
      1. FORGEMASTER_EVENT_LOG env var, if set, is used verbatim.
      2. Otherwise, defaults to <repo>/output/forgemaster_runs/<sprint_id>.jsonl.
    """
    override = os.environ.get("FORGEMASTER_EVENT_LOG", "")
    if override:
        path = Path(override)
    else:
        sprint_id = entry.get("sprint_id") or "unknown"
        path = _REPO_ROOT / "output" / "forgemaster_runs" / f"{sprint_id}.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception as exc:
        logger.warning("event log write failed: %s", exc)


def _load_empirical_stats() -> dict[tuple[str, str], dict[str, int]]:
    """
    Parse sprint_verdict events from forgemaster JSONL logs and accumulate
    pass/fail counts per (task_type, routed_model).

    Checks FORGEMASTER_EVENT_LOG env var first; falls back to scanning all
    files under output/forgemaster_runs/. Returns {} on any IO error.
    """
    stats: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"pass": 0, "fail": 0})

    log_files: list[str] = []
    override = os.environ.get("FORGEMASTER_EVENT_LOG", "")
    if override and os.path.exists(override):
        log_files = [override]
    else:
        run_dir = _REPO_ROOT / "output" / "forgemaster_runs"
        if run_dir.exists():
            log_files = [str(p) for p in sorted(run_dir.glob("*.jsonl"))]

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
                        tt = ev.get("task_type", "")
                        model = ev.get("routed_model", "")
                        outcome = ev.get("outcome", "")
                        if tt and model and outcome in ("review_pass", "review_fail"):
                            key = (tt, model)
                            if outcome == "review_pass":
                                stats[key]["pass"] += 1
                            else:
                                stats[key]["fail"] += 1
        except OSError as exc:
            logger.warning("_load_empirical_stats: could not read %s — %s", log_path, exc)

    return dict(stats)


def _compute_empirical_route(
    task_type: str,
    stats: dict[tuple[str, str], dict[str, int]],
) -> tuple[str, float]:
    """
    Pick the best model for task_type based on historical sprint pass rates.

    Only considers models with >= _EMPIRICAL_MIN_SAMPLES outcomes. Returns
    ("", 0.0) if no model has sufficient history for this task_type.
    """
    candidates = {
        model: counts
        for (tt, model), counts in stats.items()
        if tt == task_type
    }
    viable = {
        model: counts
        for model, counts in candidates.items()
        if (counts["pass"] + counts["fail"]) >= _EMPIRICAL_MIN_SAMPLES
    }
    if not viable:
        return "", 0.0

    def _score(m: str) -> tuple[float, int]:
        counts = viable[m]
        total = counts["pass"] + counts["fail"]
        return (counts["pass"] / total, total)

    best_model = max(viable, key=_score)
    counts = viable[best_model]
    total = counts["pass"] + counts["fail"]
    return best_model, round(counts["pass"] / total, 4)


def _provider_for(model: str) -> str:
    if model.startswith("claude-"):
        return "anthropic"
    if model.startswith("gemini-"):
        return "google"
    return "unknown"


def _call_anthropic(
    model: str,
    prompt: str,
    max_tokens: int = 4096,
    cached_system: str = "",
) -> tuple[str, int, int, int]:
    """
    Call an Anthropic model with a single user message.

    Returns (text, input_tokens, output_tokens, latency_ms).
    Reads CLAUDE_API_KEY at call time so .env changes are picked up without restart.

    If cached_system is provided it is sent as the system prompt with
    cache_control so subsequent calls with the same string get cache reads
    instead of writes (pair with nova_cache_prewarm).
    """
    import anthropic
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=_REPO_ROOT / ".env", override=True)
    key = os.environ.get("CLAUDE_API_KEY", "")
    if not key:
        raise RuntimeError("CLAUDE_API_KEY is not set; cannot dispatch to Anthropic.")

    t0 = time.time()
    client = anthropic.Anthropic(api_key=key)

    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if cached_system:
        kwargs["system"] = [{
            "type": "text",
            "text": cached_system,
            "cache_control": {"type": "ephemeral"},
        }]

    response = client.messages.create(**kwargs)
    text = response.content[0].text if response.content else ""
    in_tok = getattr(response.usage, "input_tokens", 0)
    out_tok = getattr(response.usage, "output_tokens", 0)
    latency_ms = int((time.time() - t0) * 1000)
    return text, in_tok, out_tok, latency_ms


def _call_gemini(prompt: str, model: str = GEMINI_MODEL, max_tokens: int = 4096) -> tuple[str, int, int, int]:
    """
    Call Gemini with a single prompt.

    Returns (text, input_tokens, output_tokens, latency_ms).
    Reads GEMINI_API_KEY at call time so .env changes are picked up without restart.
    """
    from google import genai
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=_REPO_ROOT / ".env", override=True)
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set; cannot dispatch to Gemini.")

    t0 = time.time()
    client = genai.Client(api_key=key)
    response = client.models.generate_content(model=model, contents=prompt)
    text = response.text or ""
    usage = getattr(response, "usage_metadata", None)
    in_tok = getattr(usage, "prompt_token_count", 0) if usage else 0
    out_tok = getattr(usage, "candidates_token_count", 0) if usage else 0
    latency_ms = int((time.time() - t0) * 1000)
    return text, in_tok, out_tok, latency_ms


def _dispatch(
    role: str,
    prompt: str,
    cached_system: str = "",
    model_override: str = "",
) -> tuple[str, str, int, int, int]:
    """
    Dispatch a prompt to the model assigned to *role*.

    Returns (text, model_used, input_tokens, output_tokens, latency_ms).
    Raises on unknown role or missing provider config.

    cached_system is forwarded to _call_anthropic for Anthropic lanes so
    repeated sprint turns benefit from prompt-cache reads.

    model_override, when non-empty, replaces the role's default model. Used by
    run_sprint() to wire route_ticket() decisions into the implementer dispatch
    so sprint_verdict logs accurately reflect which model actually ran.
    """
    model = model_override if model_override else _ROLE_TO_MODEL.get(role, MUNINN_MODEL)
    provider = _provider_for(model)
    if provider == "anthropic":
        text, in_tok, out_tok, lat = _call_anthropic(model, prompt, cached_system=cached_system)
    elif provider == "google":
        text, in_tok, out_tok, lat = _call_gemini(prompt, model=model)
    else:
        raise ValueError(f"Unknown model family for role={role!r}: {model!r}")
    return text, model, in_tok, out_tok, lat


def _strip_code_fences(text: str) -> str:
    """Remove a single leading/trailing markdown code fence if present."""
    stripped = text.strip()
    stripped = re.sub(r"^```[a-zA-Z0-9_+\-]*\n", "", stripped)
    stripped = re.sub(r"\n```\s*$", "", stripped)
    return stripped


def _extract_target_file(design_doc: str) -> Optional[str]:
    """
    Pull the target implementation path out of a design doc.

    Recognised labels: "Target file", "New file", "Output file", "Output path",
    "Implementation file". The path may be wrapped in backticks or written
    inline (e.g. ``Output file: output/foo.py``).
    """
    labels = r"(?:Target file|New file|Output file|Output path|Implementation file)"
    patterns = [
        rf"(?im)^\s*{labels}\s*[:\-]\s*`([^`\n]+)`",
        rf"(?im)^\s*{labels}\s*[:\-]\s*([^\s`][^\n]*?)\s*$",
    ]
    for pat in patterns:
        m = re.search(pat, design_doc)
        if m:
            return m.group(1).strip().strip("'\"")
    return None


_FENCE_RE = re.compile(r"^\s*```")


def _first_verdict_line(review_out: str) -> str:
    """
    Return the first non-empty, non-code-fence line from a reviewer output.

    Reviewers occasionally wrap their response in a ``` fence; without
    skipping it, the parser reads the fence marker as the verdict line and
    every sprint is treated as a failure.
    """
    if not review_out:
        return ""
    for raw in review_out.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _FENCE_RE.match(line):
            continue
        return line
    return ""


def _parse_review_verdict(response_text: str) -> str:
    """
    Extract a structured outcome string from reviewer output.

    Returns one of: "review_pass", "review_fail", "error".
    """
    first = _first_verdict_line(response_text)
    if not first:
        return "error"
    upper = first.upper()
    if upper.startswith("PASS"):
        return "review_pass"
    if upper.startswith("FAIL"):
        return "review_fail"
    return "error"


def _snapshot_corpus() -> dict[str, float]:
    """Return stem → mtime for every shard JSON on disk.

    Recording mtimes (not just stems) lets the biconditional check detect
    modifications and deletions, not only additions.
    """
    shard_path = Path(SHARD_DIR)
    if not shard_path.exists():
        return {}
    return {p.stem: p.stat().st_mtime for p in shard_path.glob("*.json")}


_DEFAULT_WRITE_ROOTS = ("output", "intake")


def _allowed_write_roots() -> tuple[Path, ...]:
    """
    Resolve the allowlisted subtrees that ``_write_implementation_file`` may
    write into. Defaults to ``output/`` and ``intake/`` under the repo root.
    Override via the ``FORGEMASTER_WRITE_ROOTS`` env var (comma-separated,
    paths relative to the repo root).
    """
    raw = os.environ.get("FORGEMASTER_WRITE_ROOTS", "")
    names = [n.strip() for n in raw.split(",") if n.strip()] or list(_DEFAULT_WRITE_ROOTS)
    repo = _REPO_ROOT.resolve()
    return tuple((repo / n).resolve() for n in names)


def _write_implementation_file(rel_path: str, code: str) -> str:
    """
    Write *code* to *rel_path* relative to the repo root.

    Hardened against design-doc-driven path attacks:
      - Rejects absolute paths.
      - Rejects any path containing a ``..`` component (traversal).
      - Resolved target must live under one of the allowlisted roots
        (default ``output/`` and ``intake/``; override with
        ``FORGEMASTER_WRITE_ROOTS``).

    Returns the absolute path written.
    """
    if os.path.isabs(rel_path):
        logger.error(
            "ForgemasterRuntime._write_implementation_file: rejected absolute path rel_path=%s",
            rel_path,
        )
        raise ValueError(f"Refusing to write absolute path: {rel_path}")

    parts = Path(rel_path).parts
    if ".." in parts:
        logger.error(
            "ForgemasterRuntime._write_implementation_file: rejected traversal rel_path=%s",
            rel_path,
        )
        raise ValueError(f"Refusing to write path with '..' traversal: {rel_path}")

    target = (_REPO_ROOT / rel_path).resolve()
    allowed_roots = _allowed_write_roots()

    def _is_under(child: Path, parent: Path) -> bool:
        try:
            return child.is_relative_to(parent)
        except AttributeError:  # pragma: no cover - Python < 3.9 fallback
            try:
                return os.path.commonpath((str(parent), str(child))) == str(parent)
            except ValueError:
                return False

    if not any(_is_under(target, root) for root in allowed_roots):
        logger.error(
            "ForgemasterRuntime._write_implementation_file: target outside allowlist rel_path=%s allowed=%s",
            rel_path,
            [str(r) for r in allowed_roots],
        )
        raise ValueError(
            f"Refusing to write outside allowlist: {target} (allowed: {[str(r) for r in allowed_roots]})"
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(code, encoding="utf-8")
    return str(target)


class ForgemasterRuntime:
    """
    Orchestrates the Forgemaster sprint lifecycle using NOVA as shared memory.

    Instantiated per-request in the ``nova_forgemaster_sprint`` MCP tool.
    The module-level ``_session_store`` and ``_permission_context`` singletons
    from ``nova_server.py`` are injected at construction time so this class
    remains testable in isolation.
    """

    def __init__(
        self,
        session_store: SessionStore,
        permission_context: ToolPermissionContext,
        gate: Optional[CapabilityGate] = None,
        audit_log=None,
    ) -> None:
        self._session_store = session_store
        self._permission_context = permission_context
        self._gate = gate
        self._audit = audit_log

    # ──────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────

    def bootstrap(self, sprint_id: str, shard_ids: list[str]) -> NovaSession:
        """
        Create a new ``NovaSession`` for the sprint and load the requested
        shards into context.

        If ``shard_ids`` is non-empty the shard content is appended to the
        session as a user message so downstream turns have access to it.
        The actual ``nova_shard_interact`` read path is called inline here
        (stubbed to a log note when no shards are provided).
        """
        session = self._session_store.create(sprint_id)

        if shard_ids:
            shard_context = f"[bootstrap] Loading shards into context: {', '.join(shard_ids)}"
            session = session.add_message("user", shard_context)
            session = session.add_message(
                "assistant",
                f"[bootstrap] Shards acknowledged: {', '.join(shard_ids)}",
            )
            self._session_store.update(session)
            logger.info("ForgemasterRuntime.bootstrap: loaded shards %s into session %s", shard_ids, sprint_id)
        else:
            logger.info("ForgemasterRuntime.bootstrap: no shards requested for sprint %s", sprint_id)

        return session

    def route_ticket(self, task_type: str) -> tuple[str, float]:
        """
        Map a task type to a model name and confidence score using three-tier logic:

        1. Empirical: if >= _EMPIRICAL_MIN_SAMPLES historical outcomes exist for this
           task_type and best-model pass rate > 0.6, use that model.
        2. Complexity override: task_type string contains a complexity keyword → Sonnet.
        3. Routing table + default: static prior from forgemaster/AGENTS.md.

        Confidence values: empirical pass rate | 1.0 (keyword) | 0.9 (table) | 0.0 (default).
        """
        global _empirical_routing_stats, _empirical_stats_loaded, _empirical_stats_loaded_at
        now = time.monotonic()
        if not _empirical_stats_loaded or (now - _empirical_stats_loaded_at) > _EMPIRICAL_CACHE_TTL_S:
            _empirical_routing_stats = _load_empirical_stats()
            _empirical_stats_loaded = True
            _empirical_stats_loaded_at = now

        normalized = task_type.lower().strip()

        # Tier 1: empirical model selection (model-adaptive routing)
        if _empirical_routing_stats:
            emp_model, emp_confidence = _compute_empirical_route(normalized, _empirical_routing_stats)
            if emp_model and emp_confidence > 0.6:
                logger.info(
                    "route_ticket: empirical route task_type=%r -> %s (conf=%.3f)",
                    normalized, emp_model, emp_confidence,
                )
                return emp_model, emp_confidence

        # Tier 2: complexity keyword override
        if any(kw in normalized for kw in _COMPLEX_KEYWORDS):
            return MUNINN_MODEL, 1.0

        # Tier 3: static routing table + default
        keyword_match = _ROUTING_TABLE.get(normalized)
        if keyword_match is not None:
            return keyword_match, 0.9
        return MUNINN_MODEL, 0.0

    def run_turn(
        self,
        session: NovaSession,
        role: str,
        skill_path: str,
        prompt: str,
        cached_system: str = "",
        model_override: str = "",
    ) -> tuple[NovaSession, str, SkillManifest]:
        """
        Execute a single agent turn with real LLM dispatch.

        1. Read the skill file at *skill_path* relative to the repo root.
        2. Parse @@verification / @@capabilities from the skill content.
        3. Build a prompt of (skill_content + '---' + prompt).
        4. Append as a user message to *session*.
        5. Dispatch to the model assigned to *role* (see _ROLE_TO_MODEL),
           or to model_override when provided (used by the implementer turn
           to wire route_ticket() decisions into actual dispatch).
        6. Append the response as an assistant message.
        7. Emit a JSONL event to FORGEMASTER_EVENT_LOG if configured.

        Returns the updated session, the raw response text, and the parsed
        SkillManifest for use by the caller (e.g. capability gate checks).
        """
        resolved = _REPO_ROOT / skill_path
        skill_content: str
        if resolved.exists():
            try:
                skill_content = resolved.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning(
                    "ForgemasterRuntime.run_turn: could not read skill file %s — %s",
                    resolved,
                    exc,
                )
                skill_content = f"[skill file unreadable: {skill_path}]"
        else:
            logger.warning(
                "ForgemasterRuntime.run_turn: skill file not found at %s — continuing without it",
                resolved,
            )
            skill_content = f"[skill file not found: {skill_path}]"

        # Parse @@verification / @@capabilities from the skill header block.
        active_skill = parse_skill_manifest(skill_id=skill_path, content=skill_content)

        user_content = f"{skill_content}\n\n---\n\n{prompt}"
        session = session.add_message("user", user_content)

        # Real dispatch.
        dispatch_error: Optional[str] = None
        try:
            response_text, model_used, in_tok, out_tok, latency_ms = _dispatch(
                role, user_content, cached_system=cached_system, model_override=model_override
            )
        except Exception as exc:
            logger.error("ForgemasterRuntime.run_turn: dispatch failed for %s — %s", role, exc)
            dispatch_error = str(exc)
            response_text = f"[DISPATCH FAILED: {exc}]"
            model_used = model_override if model_override else _ROLE_TO_MODEL.get(role, MUNINN_MODEL)
            in_tok = out_tok = latency_ms = 0

        session = session.add_message_with_usage("assistant", response_text, in_tok, out_tok)
        self._session_store.update(session)

        _log_event({
            "ts": datetime.now(timezone.utc).isoformat(),
            "sprint_id": session.session_id,
            "role": role,
            "skill": skill_path,
            "model": model_used,
            "provider": _provider_for(model_used),
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "latency_ms": latency_ms,
            "prompt_chars": len(user_content),
            "response_chars": len(response_text),
            "error": dispatch_error,
        })

        logger.info(
            "ForgemasterRuntime.run_turn: %s turn completed for session %s (model=%s, in=%d, out=%d, ms=%d)",
            role, session.session_id, model_used, in_tok, out_tok, latency_ms,
        )

        return session, response_text, active_skill

    def run_sprint(
        self,
        sprint_id: str,
        design_doc: str,
        shard_ids: list[str] | None = None,
        cached_system: str = "",
        task_type: str = "",
    ) -> dict:
        """
        Execute the full Forgemaster sprint lifecycle with real LLM dispatch:

        1. bootstrap — create session, load shards
        2. orchestrator turn — decompose design doc (Sonnet)
        3. planner turn — produce a concrete spec for the first ticket (Sonnet)
        4. implementer turn — generate code for the target file (Gemini)
                              code is written to disk if the design doc names a target file
        5. reviewer turn — review the implementation against the design doc (Sonnet)
        6. flush session to disk
        7. return sprint summary including the path of any file written

        Each turn's output is passed forward as context to the next turn.

        cached_system: system prompt string returned by nova_cache_prewarm. When
        provided, every Anthropic turn sends it with cache_control so the API
        serves reads (~0.1× cost) instead of writes after the first turn.
        """
        session = self.bootstrap(sprint_id, shard_ids or [])

        # Corpus snapshot before any writes — used by AuditLog biconditional check at end.
        corpus_before = _snapshot_corpus()     # set[str] of shard stems for audit check

        # ── Routing decision (logged for calibration) ─────────────────────
        routed_model, routing_confidence = self.route_ticket(task_type)
        _log_event({
            "ts": datetime.now(timezone.utc).isoformat(),
            "sprint_id": sprint_id,
            "role": "routing",
            "task_type": task_type,
            "routed_model": routed_model,
            "routing_confidence": round(routing_confidence, 4),
        })

        # ── Turn 1: Orchestrator ──────────────────────────────────────────
        session, orch_out, _ = self.run_turn(
            session,
            role="orchestrator",
            skill_path="forgemaster/skills/forgemaster-orchestrator.md",
            prompt=(
                "DESIGN DOC:\n"
                f"{design_doc}\n\n"
                "Decompose this into typed tickets per the skill above. "
                "List each ticket with its type, target file(s), and acceptance criteria."
            ),
            cached_system=cached_system,
        )

        # ── Turn 2: Planner ───────────────────────────────────────────────
        session, plan_out, _ = self.run_turn(
            session,
            role="planner",
            skill_path="forgemaster/skills/forgemaster-writing-plans.md",
            prompt=(
                "DESIGN DOC:\n"
                f"{design_doc}\n\n"
                "ORCHESTRATOR OUTPUT:\n"
                f"{orch_out}\n\n"
                "Pick the first implementation ticket and produce a complete, "
                "unambiguous spec for it. Include: exact file path, exact CLI surface, "
                "all flag names, exact output format, and acceptance criteria."
            ),
            cached_system=cached_system,
        )

        # ── Turn 3: Implementer ───────────────────────────────────────────
        # routed_model from route_ticket() is passed as model_override so the
        # calibration loop is closed: sprint_verdict records the model that
        # actually ran, not the static _ROLE_TO_MODEL default.
        session, impl_out, impl_skill = self.run_turn(
            session,
            role="implementer",
            skill_path="forgemaster/skills/forgemaster-implementation.md",
            prompt=(
                "DESIGN DOC:\n"
                f"{design_doc}\n\n"
                "PLANNER SPEC:\n"
                f"{plan_out}\n\n"
                "Implement the file described above. "
                "Return ONLY the full source code of the target file. "
                "No markdown fences. No explanation. No prose. "
                "Begin with the first line of the file and stop at the last."
            ),
            cached_system=cached_system,
            model_override=routed_model,
        )

        # Write implementer output to disk if a target file is named in the design doc.
        # Gate check: the implementer skill must declare fs.write.irrev.
        impl_file_path: Optional[str] = None
        impl_request_id: Optional[str] = None
        target_rel = _extract_target_file(design_doc)
        if target_rel:
            if self._gate:
                try:
                    impl_request_id = self._gate.check_capability_tag(
                        "fs.write.irrev",
                        True,  # is_irreversible
                        impl_skill,
                        sprint_id,
                        target=target_rel,
                    )
                except (CapabilityDenied, HITLDenied) as exc:
                    logger.warning(
                        "ForgemasterRuntime.run_sprint: file write blocked by gate "
                        "target=%s skill=%s — %s",
                        target_rel, impl_skill.skill_id, exc,
                    )
                    target_rel = None  # suppress the write

            if target_rel:
                write_ok = False
                try:
                    code = _strip_code_fences(impl_out)
                    impl_file_path = _write_implementation_file(target_rel, code)
                    write_ok = True
                    logger.info(
                        "ForgemasterRuntime.run_sprint: wrote implementation to %s",
                        impl_file_path,
                    )
                except Exception as exc:
                    logger.error(
                        "ForgemasterRuntime.run_sprint: failed to write implementation file %s — %s",
                        target_rel, exc,
                    )
                finally:
                    if impl_request_id is not None and self._audit is not None:
                        try:
                            self._audit.log_executed(
                                session_id=sprint_id,
                                request_id=impl_request_id,
                                tool_name="fs.write.irrev",
                                skill_id=impl_skill.skill_id,
                                verification=impl_skill.verification.value,
                                target=target_rel,
                                ok=write_ok,
                            )
                        except Exception as exc:
                            logger.warning(
                                "ForgemasterRuntime.run_sprint: log_executed failed — %s",
                                exc,
                            )

        # ── Turn 4: Reviewer ──────────────────────────────────────────────
        # Reviewer sees the on-disk file if written, else the raw implementer output.
        reviewer_input = impl_out
        if impl_file_path:
            try:
                reviewer_input = Path(impl_file_path).read_text(encoding="utf-8")
            except Exception as exc:
                logger.warning(
                    "ForgemasterRuntime.run_sprint: could not re-read impl file for review — %s",
                    exc,
                )

        session, review_out, _ = self.run_turn(
            session,
            role="reviewer",
            skill_path="forgemaster/skills/forgemaster-code-review.md",
            prompt=(
                "DESIGN DOC:\n"
                f"{design_doc}\n\n"
                "IMPLEMENTATION:\n"
                f"{reviewer_input}\n\n"
                "Review against the design doc's acceptance criteria. "
                "State PASS or FAIL on the first line. "
                "Then list specific issues, each with a file/line reference where applicable."
            ),
            cached_system=cached_system,
        )

        # ── Flush session to disk ─────────────────────────────────────────
        token_totals = {
            "input_tokens": session.usage.input_tokens,
            "output_tokens": session.usage.output_tokens,
            "total_tokens": session.usage.total_tokens,
        }
        self._session_store.flush(sprint_id)

        # ── Biconditional post-run audit check ───────────────────────────
        # Corpus snapshot after all writes; compared against audit log to verify
        # that every shard change is explained by an executed audit record.
        # files_written reflects the implementer-target relative path (matches
        # the value logged into the audit record as ``target``).
        corpus_after = _snapshot_corpus()
        files_written: set[str] = {target_rel} if (target_rel and impl_file_path) else set()
        biconditional_result: Optional[dict] = None
        if self._audit:
            biconditional_result = self._audit.run_biconditional_check(
                sprint_id, corpus_before, corpus_after, files_written=files_written
            )
            if not biconditional_result["passed"]:
                _log_event({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "sprint_id": sprint_id,
                    "role": "outcome",
                    "event": "biconditional_failed",
                    "unaccounted_changes": biconditional_result["unaccounted_changes"],
                    "phantom_records": biconditional_result["phantom_records"],
                    "file_unaccounted": biconditional_result["file_unaccounted"],
                    "file_phantom": biconditional_result["file_phantom"],
                })

        # ── Outcome-based reinforcement ───────────────────────────────────
        review_head = _first_verdict_line(review_out)
        sprint_passed = review_head.upper().startswith("PASS")

        # Emit structured verdict event for calibration tooling (Component 2+).
        _log_event({
            "ts": datetime.now(timezone.utc).isoformat(),
            "sprint_id": sprint_id,
            "role": "outcome",
            "event": "sprint_verdict",
            "outcome": _parse_review_verdict(review_out),
            "review_head": review_head,
            "task_type": task_type,
            "routed_model": routed_model,
            "routing_confidence": round(routing_confidence, 4),
        })
        contributing_shards = shard_ids or []

        if sprint_passed and contributing_shards:
            for shard_id in contributing_shards:
                try:
                    add_corroborated_by(shard_id, sprint_id)
                except Exception as exc:
                    logger.warning(
                        "ForgemasterRuntime.run_sprint: corroborated_by write failed "
                        "shard=%s sprint=%s — %s", shard_id, sprint_id, exc
                    )
        elif not sprint_passed and contributing_shards:
            _log_event({
                "ts": datetime.now(timezone.utc).isoformat(),
                "sprint_id": sprint_id,
                "role": "outcome",
                "event": "sprint_failed_candidates_for_review",
                "contributing_shards": contributing_shards,
                "review_head": review_head,
            })
            logger.warning(
                "ForgemasterRuntime.run_sprint: sprint %s FAILED — "
                "contributing shards flagged for review: %s",
                sprint_id, contributing_shards,
            )

        return {
            "sprint_id": sprint_id,
            "turns": 4,
            "session_id": sprint_id,
            "token_totals": token_totals,
            "implementation_file": impl_file_path,
            "review_head": review_head,
            "status": "complete",
            "outcome": "pass" if sprint_passed else "fail",
            "corroborated_shards": contributing_shards if sprint_passed else [],
            "biconditional_check": biconditional_result,
        }

    def get_permitted_lanes(
        self,
        permission_context: ToolPermissionContext,
    ) -> list[str]:
        """
        Return which agent roles are currently available given *permission_context*.

        Accepts an explicit ``permission_context`` argument rather than using
        ``self._permission_context`` so callers can evaluate hypothetical or
        alternative permission configurations without mutating the runtime
        instance (e.g. checking what lanes would be available under a more
        restricted context before dispatching).

        Roles that depend on write-capable tools are flagged as
        ``<role>:restricted`` when all write tools are denied.
        """
        all_write_tools_blocked = all(
            permission_context.blocks(tool) for tool in _WRITE_TOOLS
        )

        lanes: list[str] = []
        for role in ("orchestrator", "planner", "implementer", "reviewer"):
            if role in _WRITE_DEPENDENT_LANES and all_write_tools_blocked:
                lanes.append(f"{role}:restricted")
            else:
                lanes.append(role)
        return lanes
