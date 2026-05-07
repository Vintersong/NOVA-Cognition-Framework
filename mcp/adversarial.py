"""
adversarial.py — NÓTT adversarial contradiction pass.

Takes the top ADVERSARIAL_TOP_N shards by confidence, sends their summaries
to Gemini Flash (different training distribution from Claude), and hunts for
contradictions between claims.

Findings become `contradicts` edges in the knowledge graph. Every edge
written by this pass carries a `notes` field traceable to the specific run:
  "adversarial_pass:<pass_id>"

Runs at most once per ADVERSARIAL_MIN_INTERVAL_DAYS (stored in graph JSON
under _adversarial_meta.last_run). Requires GEMINI_API_KEY to be set.

If GEMINI_API_KEY is absent, the pass is skipped and logged as such.
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from timeutils import parse_iso, now_utc

from config import (
    ADVERSARIAL_MIN_INTERVAL_DAYS,
    ADVERSARIAL_TOP_N,
    GEMINI_MODEL,
)

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).parent.parent

_PROMPT_TEMPLATE = """\
You are an adversarial fact-checker. Review the knowledge claims below for direct contradictions.

Output rules (strict):
- For each pair of claims that directly contradict each other, output EXACTLY one line:
  CONTRADICTS: <shard_id_a> | <shard_id_b> | <one-sentence reason>
- If no contradictions exist, output exactly: NO_CONTRADICTIONS
- No other text. No headers. No explanations.

CLAIMS TO REVIEW:
---
{claims}"""


# ═══════════════════════════════════════════════════════════
# SCHEDULE GATE
# ═══════════════════════════════════════════════════════════

def should_run(graph: dict) -> bool:
    """Return True if ADVERSARIAL_MIN_INTERVAL_DAYS have passed since last run."""
    meta = graph.get("_adversarial_meta", {})
    last_run_str = meta.get("last_run")
    if not last_run_str:
        return True
    last_run = parse_iso(last_run_str)
    if last_run is None:
        return True
    return now_utc() - last_run >= timedelta(days=ADVERSARIAL_MIN_INTERVAL_DAYS)


def stamp_run(graph: dict, pass_id: str) -> None:
    """Record last-run timestamp and pass_id into the graph dict (caller must save)."""
    graph.setdefault("_adversarial_meta", {}).update({
        "last_run": now_utc().isoformat(),
        "last_pass_id": pass_id,
    })


# ═══════════════════════════════════════════════════════════
# PROMPT CONSTRUCTION
# ═══════════════════════════════════════════════════════════

def _build_prompt(shards: list[dict]) -> str:
    """Build the contradiction-hunting prompt from a list of shard index entries."""
    lines = []
    for s in shards:
        sid = s["shard_id"]
        question = s.get("guiding_question", "")
        summary = s.get("meta", {}).get("summary", "") or s.get("context_summary", "")
        lines.append(f"### {sid}\nQuestion: {question}\nClaim: {summary}")
    claims = "\n\n".join(lines)
    return _PROMPT_TEMPLATE.format(claims=claims)


# ═══════════════════════════════════════════════════════════
# GEMINI CALL
# ═══════════════════════════════════════════════════════════

def _call_gemini(prompt: str) -> str | None:
    """
    Call Gemini Flash with the adversarial prompt.
    Returns response text or None on failure.
    """
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=_REPO_ROOT / ".env", override=False)

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        logger.info("adversarial: GEMINI_API_KEY not set — pass skipped")
        return None

    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        return response.text or ""
    except Exception as exc:
        logger.warning("adversarial._call_gemini failed: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════
# OUTPUT PARSING
# ═══════════════════════════════════════════════════════════

def _parse_contradictions(
    response: str,
    valid_ids: set[str],
) -> list[dict]:
    """
    Parse lines of the form:
      CONTRADICTS: shard_a | shard_b | reason

    Returns list of {source, target, reason} dicts.
    Both IDs must be in valid_ids — guards against hallucinated IDs.
    """
    results = []
    for line in response.splitlines():
        line = line.strip()
        if not line.upper().startswith("CONTRADICTS:"):
            continue
        body = line[len("CONTRADICTS:"):].strip()
        parts = [p.strip() for p in body.split("|")]
        if len(parts) < 3:
            continue
        src, tgt, reason = parts[0], parts[1], "|".join(parts[2:]).strip()
        if src not in valid_ids or tgt not in valid_ids:
            logger.debug("adversarial: skipping hallucinated IDs %s / %s", src, tgt)
            continue
        if src == tgt:
            continue
        results.append({"source": src, "target": tgt, "reason": reason})
    return results


# ═══════════════════════════════════════════════════════════
# MAIN PASS
# ═══════════════════════════════════════════════════════════

def run_adversarial_pass(
    index: dict,
    graph: dict,
    save_graph_fn,
    add_relation_fn,
    dry_run: bool = False,
) -> dict:
    """
    Execute one adversarial contradiction pass.

    Returns a summary dict:
      {
        "pass_id": str,
        "skipped": bool,       # True if interval not met or no API key
        "shards_reviewed": int,
        "contradictions_found": int,
        "edges_written": list[dict],
        "duration_ms": float,
      }
    """
    t0 = time.monotonic()

    if not should_run(graph):
        return {
            "pass_id": None,
            "skipped": True,
            "reason": "interval_not_met",
            "shards_reviewed": 0,
            "contradictions_found": 0,
            "edges_written": [],
            "duration_ms": 0.0,
        }

    pass_id = f"adversarial_{datetime.now().strftime('%Y%m%dT%H%M%S')}"

    # Select top N by confidence, skip archived/forgotten/superseded
    candidates = [
        entry for entry in index.values()
        if "archived" not in entry.get("tags", [])
        and "forgotten" not in entry.get("tags", [])
        and not entry.get("meta", {}).get("superseded_by")
        and entry.get("meta", {}).get("summary")
    ]
    candidates.sort(key=lambda e: e.get("confidence", 0.0), reverse=True)
    top = candidates[:ADVERSARIAL_TOP_N]

    if len(top) < 2:
        return {
            "pass_id": pass_id,
            "skipped": True,
            "reason": "insufficient_shards",
            "shards_reviewed": len(top),
            "contradictions_found": 0,
            "edges_written": [],
            "duration_ms": round((time.monotonic() - t0) * 1000, 1),
        }

    prompt = _build_prompt(top)
    response = _call_gemini(prompt)

    if response is None:
        return {
            "pass_id": pass_id,
            "skipped": True,
            "reason": "no_api_key_or_call_failed",
            "shards_reviewed": len(top),
            "contradictions_found": 0,
            "edges_written": [],
            "duration_ms": round((time.monotonic() - t0) * 1000, 1),
        }

    valid_ids = {e["shard_id"] for e in top}
    contradictions = _parse_contradictions(response, valid_ids)
    edges_written = []

    for c in contradictions:
        provenance_note = f"adversarial_pass:{pass_id}"
        if not dry_run:
            add_relation_fn(
                c["source"], c["target"], "contradicts",
                notes=provenance_note,
                reason=c["reason"],
            )
        edges_written.append({
            "source": c["source"],
            "target": c["target"],
            "reason": c["reason"],
            "pass_id": pass_id,
        })

    if not dry_run:
        stamp_run(graph, pass_id)
        save_graph_fn(graph)

    return {
        "pass_id": pass_id,
        "skipped": False,
        "shards_reviewed": len(top),
        "contradictions_found": len(contradictions),
        "edges_written": edges_written,
        "duration_ms": round((time.monotonic() - t0) * 1000, 1),
    }
