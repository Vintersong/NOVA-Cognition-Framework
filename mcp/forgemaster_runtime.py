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
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import HUGINN_MODEL, MUNINN_MODEL, GEMINI_MODEL
from permissions import ToolPermissionContext
from session_store import SessionStore, NovaSession

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
    # stitch lane (no config constant — UI model not yet defined)
    "ui": "stitch",
    "frontend": "stitch",
    "mockup": "stitch",
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

# Role-to-model mapping for the 4-turn sprint pipeline.
_ROLE_TO_MODEL: dict[str, str] = {
    "orchestrator": MUNINN_MODEL,
    "planner":      MUNINN_MODEL,
    "implementer":  GEMINI_MODEL,
    "reviewer":     MUNINN_MODEL,
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


def _provider_for(model: str) -> str:
    if model.startswith("claude-"):
        return "anthropic"
    if model.startswith("gemini-"):
        return "google"
    return "unknown"


def _call_anthropic(model: str, prompt: str, max_tokens: int = 4096) -> tuple[str, int, int, int]:
    """
    Call an Anthropic model with a single user message.

    Returns (text, input_tokens, output_tokens, latency_ms).
    Reads CLAUDE_API_KEY at call time so .env changes are picked up without restart.
    """
    import anthropic
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=_REPO_ROOT / ".env", override=True)
    key = os.environ.get("CLAUDE_API_KEY", "")
    if not key:
        raise RuntimeError("CLAUDE_API_KEY is not set; cannot dispatch to Anthropic.")

    t0 = time.time()
    client = anthropic.Anthropic(api_key=key)
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text if response.content else ""
    in_tok = getattr(response.usage, "input_tokens", 0)
    out_tok = getattr(response.usage, "output_tokens", 0)
    latency_ms = int((time.time() - t0) * 1000)
    return text, in_tok, out_tok, latency_ms


def _call_gemini(prompt: str, max_tokens: int = 4096) -> tuple[str, int, int, int]:
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
    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    text = response.text or ""
    usage = getattr(response, "usage_metadata", None)
    in_tok = getattr(usage, "prompt_token_count", 0) if usage else 0
    out_tok = getattr(usage, "candidates_token_count", 0) if usage else 0
    latency_ms = int((time.time() - t0) * 1000)
    return text, in_tok, out_tok, latency_ms


def _dispatch(role: str, prompt: str) -> tuple[str, str, int, int, int]:
    """
    Dispatch a prompt to the model assigned to *role*.

    Returns (text, model_used, input_tokens, output_tokens, latency_ms).
    Raises on unknown role or missing provider config.
    """
    model = _ROLE_TO_MODEL.get(role, MUNINN_MODEL)
    provider = _provider_for(model)
    if provider == "anthropic":
        text, in_tok, out_tok, lat = _call_anthropic(model, prompt)
    elif provider == "google":
        text, in_tok, out_tok, lat = _call_gemini(prompt)
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

    Looks for patterns like:  Target file: `utilities/foo.py`
    or:                       New file: `utilities/foo.py`
    Returns the path string or None if no match.
    """
    patterns = [
        r"[Tt]arget file[^`]*`([^`]+)`",
        r"[Nn]ew file[^`]*`([^`]+)`",
    ]
    for pat in patterns:
        m = re.search(pat, design_doc)
        if m:
            return m.group(1).strip()
    return None


def _write_implementation_file(rel_path: str, code: str) -> str:
    """
    Write *code* to *rel_path* relative to the repo root.

    Only allows writes inside the repo root tree (no escape via '..').
    Returns the absolute path written.
    """
    target = (_REPO_ROOT / rel_path).resolve()
    repo = _REPO_ROOT.resolve()
    try:
        within_repo = target.is_relative_to(repo)
    except AttributeError:  # pragma: no cover - Python < 3.9 fallback
        try:
            within_repo = os.path.commonpath((str(repo), str(target))) == str(repo)
        except ValueError:
            within_repo = False
    if not within_repo:
        logger.error(
            "ForgemasterRuntime._write_implementation_file: rejected path escape rel_path=%s resolved=%s repo=%s",
            rel_path,
            target,
            repo,
        )
        raise ValueError(f"Refusing to write outside repo root: {target}")
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
    ) -> None:
        self._session_store = session_store
        self._permission_context = permission_context

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

    def route_ticket(self, task_type: str) -> str:
        """
        Map a task type to a model name using the routing table from
        ``forgemaster/AGENTS.md``.

        Complexity override: tasks whose type string contains any of the
        ``_COMPLEX_KEYWORDS`` are promoted to ``claude-sonnet`` regardless
        of the routing table (borrows the keyword-routing heuristic from
        hermes-agent smart_model_routing.py).

        Defaults to ``claude-sonnet`` for unknown task types.
        """
        normalized = task_type.lower().strip()
        if any(kw in normalized for kw in _COMPLEX_KEYWORDS):
            return MUNINN_MODEL
        return _ROUTING_TABLE.get(normalized, MUNINN_MODEL)

    def run_turn(
        self,
        session: NovaSession,
        role: str,
        skill_path: str,
        prompt: str,
    ) -> tuple[NovaSession, str]:
        """
        Execute a single agent turn with real LLM dispatch.

        1. Read the skill file at *skill_path* relative to the repo root.
        2. Build a prompt of (skill_content + '---' + prompt).
        3. Append as a user message to *session*.
        4. Dispatch to the model assigned to *role* (see _ROLE_TO_MODEL).
        5. Append the response as an assistant message.
        6. Emit a JSONL event to FORGEMASTER_EVENT_LOG if configured.

        Returns the updated session and the raw response text.
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

        user_content = f"{skill_content}\n\n---\n\n{prompt}"
        session = session.add_message("user", user_content)

        # Real dispatch.
        dispatch_error: Optional[str] = None
        try:
            response_text, model_used, in_tok, out_tok, latency_ms = _dispatch(role, user_content)
        except Exception as exc:
            logger.error("ForgemasterRuntime.run_turn: dispatch failed for %s — %s", role, exc)
            dispatch_error = str(exc)
            response_text = f"[DISPATCH FAILED: {exc}]"
            model_used = _ROLE_TO_MODEL.get(role, MUNINN_MODEL)
            in_tok = out_tok = latency_ms = 0

        session = session.add_message("assistant", response_text)
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

        return session, response_text

    def run_sprint(
        self,
        sprint_id: str,
        design_doc: str,
        shard_ids: list[str] | None = None,
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
        """
        session = self.bootstrap(sprint_id, shard_ids or [])

        # ── Turn 1: Orchestrator ──────────────────────────────────────────
        session, orch_out = self.run_turn(
            session,
            role="orchestrator",
            skill_path="forgemaster/skills/forgemaster-orchestrator.md",
            prompt=(
                "DESIGN DOC:\n"
                f"{design_doc}\n\n"
                "Decompose this into typed tickets per the skill above. "
                "List each ticket with its type, target file(s), and acceptance criteria."
            ),
        )

        # ── Turn 2: Planner ───────────────────────────────────────────────
        session, plan_out = self.run_turn(
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
        )

        # ── Turn 3: Implementer ───────────────────────────────────────────
        session, impl_out = self.run_turn(
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
        )

        # Write implementer output to disk if a target file is named in the design doc.
        impl_file_path: Optional[str] = None
        target_rel = _extract_target_file(design_doc)
        if target_rel:
            try:
                code = _strip_code_fences(impl_out)
                impl_file_path = _write_implementation_file(target_rel, code)
                logger.info(
                    "ForgemasterRuntime.run_sprint: wrote implementation to %s",
                    impl_file_path,
                )
            except Exception as exc:
                logger.error(
                    "ForgemasterRuntime.run_sprint: failed to write implementation file %s — %s",
                    target_rel, exc,
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

        session, review_out = self.run_turn(
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
        )

        # ── Flush session to disk ─────────────────────────────────────────
        token_totals = {
            "input_tokens": session.usage.input_tokens,
            "output_tokens": session.usage.output_tokens,
            "total_tokens": session.usage.total_tokens,
        }
        self._session_store.flush(sprint_id)

        return {
            "sprint_id": sprint_id,
            "turns": 4,
            "session_id": sprint_id,
            "token_totals": token_totals,
            "implementation_file": impl_file_path,
            "review_head": review_out.strip().splitlines()[0] if review_out.strip() else "",
            "status": "complete",
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
