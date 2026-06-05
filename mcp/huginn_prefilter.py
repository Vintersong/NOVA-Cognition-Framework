"""
huginn_prefilter.py — Orchestrator pre-filter for the HUGINN agent pipeline.

Does the cheap keyword + confidence pass that the HUGINN agent should NOT do
itself. Called by nova_huginn_candidates (MCP tool) or directly from any
orchestration layer before spawning a HUGINN subagent.

Pipeline:
  1. Tokenize query into lowercase words
  2. Keep shards where any token appears in guiding_question or context_summary
  3. If fewer than min_survivors match, fall back to top-N by confidence
  4. Sort by confidence descending, cap at max_candidates
  5. Return list of dicts with only the fields HUGINN needs to score

HUGINN receives this list in its prompt and scores without any tool calls.
"""

from __future__ import annotations

import re
from typing import Any


_STOP_WORDS: frozenset[str] = frozenset({
    # English function words
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "and", "or", "but", "not", "no", "so", "yet", "both", "either",
    "what", "which", "who", "this", "that", "these", "those", "how",
    # Corpus-specific: appears in nearly every shard, zero discrimination power
    "nova", "shard", "shards", "discussed", "document", "reference",
    "contain", "contains", "what", "does",
})


def _tokenize(text: str) -> set[str]:
    """Lowercase word tokens, stop-words removed, min length 3."""
    tokens = re.findall(r"[a-z0-9_]+", text.lower())
    return {t for t in tokens if len(t) >= 3 and t not in _STOP_WORDS}


def prefilter(
    query: str,
    index: dict[str, Any],
    max_candidates: int = 20,
    min_survivors: int = 5,
) -> list[dict[str, Any]]:
    """
    Return a pre-filtered candidate list ready to pass to a HUGINN agent.

    Each entry: {"id", "guiding_question", "context_summary", "confidence"}

    Args:
        query:          The retrieval query string.
        index:          Full shard index dict from store.update_index().
        max_candidates: Hard cap on returned candidates (default 20).
        min_survivors:  If keyword match returns fewer than this, fall back
                        to top-N by confidence so HUGINN always gets something.
    """
    query_tokens = _tokenize(query)
    if not query_tokens:
        # Degenerate query — fall back to top-N by confidence
        return _top_by_confidence(index, max_candidates)

    scored: list[tuple[int, float, dict[str, Any]]] = []  # (match_count, confidence, candidate)
    unmatched: list[dict[str, Any]] = []

    for shard_id, entry in index.items():
        tags = entry.get("tags", [])
        if "archived" in tags or "forgotten" in tags:
            continue

        gq = entry.get("guiding_question", "") or ""
        cs = entry.get("context_summary", "") or ""
        searchable_tokens = _tokenize(gq + " " + cs)
        confidence = round(float(entry.get("confidence", 1.0)), 4)

        candidate = {
            "id": shard_id,
            "guiding_question": gq,
            "context_summary": cs[:300],
            "confidence": confidence,
        }

        match_count = len(query_tokens & searchable_tokens)
        if match_count > 0:
            scored.append((match_count, confidence, candidate))
        else:
            unmatched.append(candidate)

    # Sort by (match_count DESC, confidence DESC) — specificity first
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    matched = [c for _, _, c in scored]

    # Pad with highest-confidence unmatched if below min_survivors
    if len(matched) < min_survivors:
        unmatched.sort(key=lambda x: x["confidence"], reverse=True)
        matched.extend(unmatched[:min_survivors - len(matched)])

    return matched[:max_candidates]


def _top_by_confidence(index: dict[str, Any], n: int) -> list[dict[str, Any]]:
    candidates = []
    for shard_id, entry in index.items():
        tags = entry.get("tags", [])
        if "archived" in tags or "forgotten" in tags:
            continue
        candidates.append({
            "id": shard_id,
            "guiding_question": entry.get("guiding_question", ""),
            "context_summary": (entry.get("context_summary", "") or "")[:300],
            "confidence": round(float(entry.get("confidence", 1.0)), 4),
        })
    candidates.sort(key=lambda x: x["confidence"], reverse=True)
    return candidates[:n]
