"""
external_retrieval.py — NOVA External Retrieval Deliberation Pipeline v1.0

5-agent pipeline for validating external information before ingesting it as shards.

Flow:
  Agent 0 (Haiku) — knowledge-based retrieval pass
  Agents 1-3 (Haiku, parallel) — validate, challenge, synthesize
  Arbiter (Sonnet/Opus) — ACCEPT / PARTIAL / REJECT + confidence score
  Shard write (on ACCEPT/PARTIAL) — claim shard + debate log, linked via graph

Implementation note: all Anthropic API calls use the SYNC client wrapped in
run_in_executor. The MCP server's asyncio event loop does not play well with
AsyncAnthropic — using sync clients in a thread pool avoids the deadlock.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from functools import partial
from uuid import uuid4

import anthropic

from config import (
    CLAUDE_API_KEY,
    HUGINN_MODEL,
    NOVA_EXTERNAL_ARBITER_MODEL,
    NOVA_EXTERNAL_RETRIEVAL_TIMEOUT,
    SHARD_DIR,
)
from graph import add_relation, add_shard_to_graph
from outputs import ExternalDeliberation, ExternalRetrievalResult
from permissions import denial_reject, is_blocked
from reject import RejectCode, reject_model
from schemas import ExternalRetrievalInput
from store import get_unique_filename, patch_index_entry, sanitize_filename, save_shard
from tool_registry import nova_tool

logger = logging.getLogger(__name__)

# ── Cost model (Anthropic pricing, May 2026) ──────────────────────────────────
# Haiku 4.5: $0.80/M in, $4.00/M out
# Sonnet 4.6: $3.00/M in, $15.00/M out (arbiter default)
_HAIKU_IN   = 0.80  / 1_000_000
_HAIKU_OUT  = 4.00  / 1_000_000
_SONNET_IN  = 3.00  / 1_000_000
_SONNET_OUT = 15.00 / 1_000_000

_COST_ESTIMATE = round(
    (500  * _HAIKU_IN  + 800 * _HAIKU_OUT)        # Agent 0
    + 3 * (1000 * _HAIKU_IN  + 400 * _HAIKU_OUT)  # Agents 1-3
    + (2000 * _SONNET_IN + 200 * _SONNET_OUT),     # Arbiter
    4,
)


# ── Sync API helpers (run inside thread executor) ─────────────────────────────

def _extract_text(resp: anthropic.types.Message) -> str:
    """Pull all text content from an Anthropic response object."""
    parts: list[str] = []
    for block in resp.content:
        if hasattr(block, "text"):
            parts.append(block.text)
        elif getattr(block, "type", None) == "tool_result":
            content = getattr(block, "content", None)
            if isinstance(content, list):
                for sub in content:
                    if hasattr(sub, "text"):
                        parts.append(sub.text)
            elif isinstance(content, str):
                parts.append(content)
    return "\n".join(parts).strip() or "(no content)"


def _sync_call(model: str, prompt: str, max_tokens: int, timeout: float) -> str:
    """Blocking Anthropic call. Runs in a thread via run_in_executor."""
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY, timeout=timeout)
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return _extract_text(resp)


async def _call(
    model: str,
    prompt: str,
    max_tokens: int,
    timeout: float,
    label: str,
) -> str | Exception:
    """Run _sync_call in a thread executor with asyncio timeout. Returns Exception on failure."""
    loop = asyncio.get_running_loop()
    fn = partial(_sync_call, model, prompt, max_tokens, timeout)
    try:
        return await asyncio.wait_for(loop.run_in_executor(None, fn), timeout=timeout + 5)
    except asyncio.TimeoutError:
        err = TimeoutError(f"{label} timed out after {timeout}s")
        logger.warning("external_retrieval: %s", err)
        return err
    except Exception as exc:
        logger.warning("external_retrieval: %s failed: %s", label, exc)
        return exc


# ── Shard writers ─────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_shard(
    guiding_question: str,
    intent: str,
    theme: str,
    confidence: float,
    history: list[dict],
    extra_tags: dict,
    filename_prefix: str,
) -> str:
    slug = sanitize_filename(filename_prefix)
    filename = get_unique_filename(slug)
    filepath = os.path.join(SHARD_DIR, filename)
    shard_id = filename.replace(".json", "")
    now = _now()

    shard_data: dict = {
        "shard_id": shard_id,
        "guiding_question": guiding_question,
        "conversation_history": history,
        "meta_tags": {
            "intent": intent,
            "theme": theme,
            "usage_count": 1,
            "last_used": now,
            "confidence": confidence,
            "enrichment_status": "pending",
            "project_context": "",
            "quarantine_until": None,
            "validity_window": None,
            "superseded_by": None,
            **extra_tags,
        },
    }

    save_shard(filepath, shard_data)
    patch_index_entry(shard_id, shard_data)
    add_shard_to_graph(shard_id, shard_data)
    return shard_id


def _write_claim_shard(
    query: str, claim: str, confidence: float, verdict: str,
    debate_shard_id: str, pipeline_run_id: str,
) -> str:
    now = _now()
    return _make_shard(
        guiding_question=f"External retrieval — {query[:80]}",
        intent="research",
        theme="external_claim",
        confidence=confidence,
        history=[{"timestamp": now, "user": f"External query: {query}", "ai": claim}],
        extra_tags={
            "source": "external_retrieval_pipeline",
            "verdict": verdict,
            "pipeline_run_id": pipeline_run_id,
            "debate_log_shard": debate_shard_id,
            "retrieved": now,
            "query": query,
        },
        filename_prefix=f"external_claim_{query[:28]}",
    )


def _write_debate_shard(
    query: str, raw_results: str, validation: str, challenge: str,
    synthesis: str, verdict: str, rejection_reason: str, pipeline_run_id: str,
) -> str:
    now = _now()
    body = (
        f"## Query\n{query}\n\n"
        f"## Raw Results (Agent 0)\n{raw_results}\n\n"
        f"## Validation (Agent 1)\n{validation}\n\n"
        f"## Challenge (Agent 2)\n{challenge}\n\n"
        f"## Synthesis (Agent 3)\n{synthesis}\n\n"
        f"## Arbiter Verdict\n{verdict}"
        + (f"\n\nRejection reason: {rejection_reason}" if rejection_reason else "")
    )
    return _make_shard(
        guiding_question=f"Debate log — external retrieval: {query[:60]}",
        intent="archive",
        theme="external_debate_log",
        confidence=0.6,
        history=[{"timestamp": now, "user": f"Debate log for pipeline run {pipeline_run_id}", "ai": body}],
        extra_tags={
            "source": "external_retrieval_pipeline",
            "pipeline_run_id": pipeline_run_id,
        },
        filename_prefix=f"external_debate_{query[:28]}",
    )


# ── Arbiter response parsing ──────────────────────────────────────────────────

def _parse_arbiter(text: str, synthesis: str) -> dict:
    clean = text.strip()
    # Strip markdown code fence: ```json\n{...}\n``` → content is parts[1]
    if clean.startswith("```"):
        parts = clean.split("```", 2)
        clean = parts[1] if len(parts) >= 2 else clean
        if clean.startswith("json"):
            clean = clean[4:]
    try:
        return json.loads(clean.strip())
    except (json.JSONDecodeError, ValueError):
        # Check ACCEPT/PARTIAL before REJECT — "rejection_reason" contains "reject"
        upper = text.upper()
        if '"VERDICT": "ACCEPT"' in upper or "'VERDICT': 'ACCEPT'" in upper:
            return {"verdict": "ACCEPT", "claim": synthesis, "confidence": 0.6, "rejection_reason": ""}
        if '"VERDICT": "PARTIAL"' in upper or "'VERDICT': 'PARTIAL'" in upper:
            return {"verdict": "PARTIAL", "claim": synthesis, "confidence": 0.5, "rejection_reason": ""}
        if "ACCEPT" in upper and "REJECT" not in upper:
            return {"verdict": "ACCEPT", "claim": synthesis, "confidence": 0.6, "rejection_reason": ""}
        if "PARTIAL" in upper:
            return {"verdict": "PARTIAL", "claim": synthesis, "confidence": 0.5, "rejection_reason": ""}
        return {"verdict": "REJECT", "claim": "", "confidence": 0.0, "rejection_reason": text}


def _err(reason: str, code: RejectCode = RejectCode.PRECONDITION_FAILED) -> dict:
    """A pipeline failure. ``code`` is what the tool turns the dict into on the
    wire — see the reject mapping in ``nova_external_retrieval``."""
    return {
        "verdict": "error",
        "code": code.value,
        "claim": "",
        "confidence": 0.0,
        "shard_id": None,
        "debate_shard_id": None,
        "cost_estimate": _COST_ESTIMATE,
        "rejection_reason": reason,
    }


# ── Core pipeline ─────────────────────────────────────────────────────────────

async def run_external_retrieval(query: str, context: str = "") -> dict:
    """
    Execute the full external retrieval deliberation pipeline.

    Returns dict: {verdict, claim, confidence, shard_id,
                   debate_shard_id, cost_estimate, rejection_reason}
    """
    if not CLAUDE_API_KEY:
        return _err(
            "CLAUDE_API_KEY not set — external retrieval requires Anthropic API access",
            RejectCode.DEPENDENCY_MISSING,
        )

    cost_cap = float(os.environ.get("NOVA_EXTERNAL_COST_CAP", "0.10"))
    if _COST_ESTIMATE > cost_cap:
        return {
            "verdict": "cap_exceeded",
            "claim": "",
            "confidence": 0.0,
            "shard_id": None,
            "debate_shard_id": None,
            "cost_estimate": _COST_ESTIMATE,
            "rejection_reason": f"Estimated cost ${_COST_ESTIMATE:.4f} exceeds cap ${cost_cap:.4f}",
        }

    pipeline_run_id = str(uuid4())[:8]
    t = float(os.environ.get("NOVA_EXTERNAL_RETRIEVAL_TIMEOUT", str(NOVA_EXTERNAL_RETRIEVAL_TIMEOUT)))
    arbiter_t = float(os.environ.get("NOVA_EXTERNAL_ARBITER_TIMEOUT", "90.0"))
    arbiter_model = os.environ.get("NOVA_EXTERNAL_ARBITER_MODEL", NOVA_EXTERNAL_ARBITER_MODEL)

    # ── Agent 0: retrieval ────────────────────────────────────────────────────
    retrieval_prompt = (
        "You are a retrieval agent. Provide all factual information you know "
        "about the following query. For each claim, note its likely source type "
        "(widely documented, single-source, potentially outdated, uncertain). "
        "Be explicit about your knowledge cutoff and what you cannot confirm. "
        "Do not speculate. List claims with source characterisation.\n\n"
        f"Query: {query}"
    )
    raw = await _call(HUGINN_MODEL, retrieval_prompt, 1500, t, "Agent 0 (retrieval)")
    if isinstance(raw, Exception):
        return _err(str(raw))
    if not raw or raw == "(no content)":
        return {
            "verdict": "no_results", "claim": "", "confidence": 0.0,
            "shard_id": None, "debate_shard_id": None,
            "cost_estimate": _COST_ESTIMATE,
            "rejection_reason": "Agent 0 returned no content",
        }

    ctx_block = f"\nAdditional context: {context}\n" if context else ""

    # ── Agents 1 & 2: parallel debate ─────────────────────────────────────────
    validator_prompt = (
        "You are a validation agent. Review the following information and identify:\n"
        "1. Claims that are well-supported and widely documented\n"
        "2. Claims that appear in only one source or are uncertain\n"
        "3. Claims that seem potentially outdated\n"
        f"{ctx_block}\n"
        f"Information:\n{raw}\n\n"
        "Return structured validation analysis."
    )
    challenger_prompt = (
        "You are an adversarial agent. Review the following information and identify:\n"
        "1. What important context is missing?\n"
        "2. What claims might be contested or have known counterarguments?\n"
        "3. What would a skeptic say about this?\n"
        f"{ctx_block}\n"
        f"Information:\n{raw}\n\n"
        "Return structured challenge analysis."
    )

    validation, challenge = await asyncio.gather(
        _call(HUGINN_MODEL, validator_prompt, 600, t, "Agent 1 (validator)"),
        _call(HUGINN_MODEL, challenger_prompt, 600, t, "Agent 2 (challenger)"),
    )
    if isinstance(validation, Exception):
        validation = f"(validation unavailable: {validation})"
    if isinstance(challenge, Exception):
        challenge = f"(challenge unavailable: {challenge})"

    # ── Agent 3: synthesizer ──────────────────────────────────────────────────
    synthesizer_prompt = (
        "You have the following information and two analyses of it.\n"
        "Identify the defensible core claim: what can be stated with reasonable "
        "confidence after accounting for the validation and challenge?\n\n"
        f"Information:\n{raw}\n\n"
        f"Validation:\n{validation}\n\n"
        f"Challenge:\n{challenge}\n\n"
        "Return: core claim, confidence level (HIGH/MEDIUM/LOW), key caveats."
    )
    synthesis = await _call(HUGINN_MODEL, synthesizer_prompt, 500, t, "Agent 3 (synthesizer)")
    if isinstance(synthesis, Exception):
        synthesis = f"(synthesis unavailable: {synthesis})"

    # ── Arbiter ───────────────────────────────────────────────────────────────
    arbiter_prompt = (
        "You are the final arbiter of an information retrieval pipeline.\n"
        "You have received retrieved information and three agent analyses.\n\n"
        "Your task:\n"
        "1. Render a verdict: ACCEPT, PARTIAL, or REJECT\n"
        "2. If ACCEPT or PARTIAL: state the clean claim in one sentence and assign "
        "a confidence score 0.0-1.0\n"
        "3. If REJECT: state why the information is insufficient or unreliable\n\n"
        f"Retrieved information:\n{raw}\n\n"
        f"Validation analysis:\n{validation}\n\n"
        f"Challenge analysis:\n{challenge}\n\n"
        f"Synthesis:\n{synthesis}\n\n"
        "Be conservative. When in doubt, PARTIAL or REJECT.\n\n"
        "Respond in JSON only:\n"
        '{"verdict": "ACCEPT|PARTIAL|REJECT", "claim": "...", "confidence": 0.0, "rejection_reason": "..."}'
    )
    arbiter_raw = await _call(arbiter_model, arbiter_prompt, 400, arbiter_t, "Arbiter")
    if isinstance(arbiter_raw, Exception):
        return _err(f"Arbiter failed: {arbiter_raw}")

    arbiter      = _parse_arbiter(arbiter_raw, str(synthesis))
    verdict      = arbiter.get("verdict", "REJECT").upper()
    claim        = arbiter.get("claim", "")
    confidence   = float(arbiter.get("confidence", 0.0))
    reject_reason = arbiter.get("rejection_reason", "")

    # ── Shard ingestion ───────────────────────────────────────────────────────
    claim_shard_id  = None
    debate_shard_id = None

    if verdict in ("ACCEPT", "PARTIAL"):
        try:
            loop = asyncio.get_running_loop()
            debate_shard_id = await loop.run_in_executor(None, lambda: _write_debate_shard(
                query=query, raw_results=str(raw), validation=str(validation),
                challenge=str(challenge), synthesis=str(synthesis),
                verdict=verdict, rejection_reason=reject_reason,
                pipeline_run_id=pipeline_run_id,
            ))
            claim_shard_id = await loop.run_in_executor(None, lambda: _write_claim_shard(
                query=query, claim=claim, confidence=confidence,
                verdict=verdict, debate_shard_id=debate_shard_id,
                pipeline_run_id=pipeline_run_id,
            ))
            add_relation(claim_shard_id, debate_shard_id, "references")
            logger.info(
                "external_retrieval [%s]: %s — claim=%s debate=%s",
                pipeline_run_id, verdict, claim_shard_id, debate_shard_id,
            )
        except Exception as exc:
            logger.warning("external_retrieval: shard write failed: %s", exc)
    else:
        logger.info("external_retrieval [%s]: REJECT — %s", pipeline_run_id, reject_reason)

    return {
        "verdict": verdict,
        "claim": claim,
        "confidence": confidence,
        "shard_id": claim_shard_id,
        "debate_shard_id": debate_shard_id,
        "cost_estimate": _COST_ESTIMATE,
        "rejection_reason": reject_reason,
    }


# ── MCP tool registration ─────────────────────────────────────────────────────

def register_external_retrieval_tools(mcp) -> None:

    @nova_tool(mcp, name="nova_external_retrieval")
    async def nova_external_retrieval(params: ExternalRetrievalInput) -> ExternalRetrievalResult:
        """
        External retrieval deliberation pipeline (v1.0).

        Fires when HUGINN internal retrieval confidence is insufficient or
        current external knowledge is required. Pipeline:
          Agent 0 (Haiku) — knowledge retrieval pass
          Agents 1-3 (Haiku, parallel) — validate / challenge / synthesize
          Arbiter (Sonnet) — ACCEPT / PARTIAL / REJECT + confidence score

        On ACCEPT or PARTIAL: writes a claim shard and a debate-log shard,
        linked via a references graph edge.
        On REJECT: logs reason; no shard is written.

        Cost guard fires before any API calls — aborts if estimated cost
        exceeds NOVA_EXTERNAL_COST_CAP (env var, default $0.10).

        Returns JSON: {verdict, claim, confidence, shard_id,
                       debate_shard_id, cost_estimate, rejection_reason}
        """
        if is_blocked("nova_external_retrieval"):
            return denial_reject("nova_external_retrieval")

        result = await run_external_retrieval(params.query, params.context or "")
        # The cost cap is a refusal — the pipeline never ran — so it gets the
        # reject envelope, which also means the middleware marks it isError.
        # Every other verdict, REJECT included, is a real deliberation outcome.
        if result.get("verdict") == "cap_exceeded":
            return reject_model(
                RejectCode.PRECONDITION_FAILED,
                result.get("rejection_reason", "Estimated cost exceeds the cap."),
                retryable=False,
                hint="Raise NOVA_EXTERNAL_COST_CAP or narrow the query.",
                extra={"cost_estimate": result.get("cost_estimate", 0.0)},
            )
        if result.get("verdict") == "error":
            return reject_model(
                RejectCode(result.get("code", RejectCode.PRECONDITION_FAILED)),
                result.get("rejection_reason", "External retrieval failed."),
                extra={"cost_estimate": result.get("cost_estimate", 0.0)},
            )
        return ExternalDeliberation(**result)
