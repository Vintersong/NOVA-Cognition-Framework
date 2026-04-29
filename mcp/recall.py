"""
recall.py — Hook recall for NOVA.

Provides hook_recall(): synchronous, confidence-floored, summary-only retrieval
for use in Claude Code hooks (UserPromptSubmit, PostToolUse).

Confidence floor semantics: uses the shard's stored meta_tags.confidence,
not the retrieval score. A shard that scores high on relevance but has low
stored confidence (due to decay or contradiction) is excluded.

Cache: in-memory LRU with TTL keyed on (query_hash, top_k, min_confidence).
TTL controlled by NOVA_RECALL_CACHE_TTL env var (default 300 seconds).
"""

from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime
from pathlib import Path

from config import NOVA_AGENT_INFERENCE_WEIGHT, NOVA_PROJECT_CONTEXT, QUARANTINE_PENALTY, SHARD_DIR
from store import load_index, passes_state_gate
from access_log import log_shard_access

_CACHE_TTL = float(os.environ.get("NOVA_RECALL_CACHE_TTL", "300"))

# (cache_key) → (expires_at, result)
_cache: dict[tuple, tuple[float, list[dict]]] = {}


# ═══════════════════════════════════════════════════════════
# CACHE
# ═══════════════════════════════════════════════════════════

def _make_key(query: str, top_k: int, min_confidence: float) -> tuple:
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
    return (digest, top_k, min_confidence)


def _cache_get(key: tuple) -> list[dict] | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    expires_at, result = entry
    if time.monotonic() < expires_at:
        return result
    del _cache[key]
    return None


def _cache_set(key: tuple, value: list[dict]) -> None:
    _cache[key] = (time.monotonic() + _CACHE_TTL, value)


def clear_cache() -> None:
    """Flush the recall cache. Useful in tests."""
    _cache.clear()


# ═══════════════════════════════════════════════════════════
# LOCAL RETRIEVAL
# ═══════════════════════════════════════════════════════════

def _local_score(query: str, index: dict) -> list[tuple[str, float]]:
    """
    Token-overlap + Jaccard blend × confidence × trust.
    Mirrors Huginn._local_retrieve — kept local so recall.py has no async dep.
    """
    msg_tokens = set(query.lower().split())
    scored = []

    for shard_id, entry in index.items():
        confidence = entry.get("confidence", 1.0)
        trust = entry.get("trust_score", 1.0)

        searchable = " ".join([
            entry.get("guiding_question", ""),
            entry.get("context_summary", ""),
            " ".join(entry.get("context_topics", [])),
            entry.get("meta", {}).get("theme", ""),
            entry.get("meta", {}).get("intent", ""),
            entry.get("meta", {}).get("summary", ""),
        ]).lower()

        search_tokens = set(searchable.split())
        overlap = msg_tokens & search_tokens
        union = msg_tokens | search_tokens
        base_score = len(overlap) / max(len(msg_tokens), 1)
        jaccard = len(overlap) / max(len(union), 1)
        blended = (0.6 * base_score + 0.4 * jaccard) * confidence * trust

        if entry.get("meta", {}).get("source", "agent_inference") == "agent_inference":
            blended *= NOVA_AGENT_INFERENCE_WEIGHT

        # Penalise shards still in quarantine window
        quarantine_until = entry.get("meta", {}).get("quarantine_until")
        if quarantine_until:
            try:
                if datetime.fromisoformat(quarantine_until) > datetime.now():
                    blended *= QUARANTINE_PENALTY
            except (ValueError, TypeError):
                pass

        if blended > 0.0:
            scored.append((shard_id, blended))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


# ═══════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════

def hook_recall(
    query: str,
    top_k: int = 3,
    min_confidence: float = 0.85,
) -> list[dict]:
    """
    Retrieve up to top_k shards whose stored confidence >= min_confidence.

    Returns summary headers only — never conversation bodies.
    Returns an empty list if nothing clears the confidence floor.
    Results are cached for NOVA_RECALL_CACHE_TTL seconds (default 300s).

    Each result dict:
        shard_id         str
        summary          str   (~50 tokens from meta_tags.summary)
        confidence       float (stored meta_tags.confidence)
        score            float (retrieval relevance score)
        guiding_question str
    """
    key = _make_key(query, top_k, min_confidence)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    index = load_index()

    # Pre-filter: confidence floor + exclude archived/forgotten + state gate
    eligible = {
        sid: entry for sid, entry in index.items()
        if entry.get("confidence", 1.0) >= min_confidence
        and "archived" not in entry.get("tags", [])
        and "forgotten" not in entry.get("tags", [])
        and passes_state_gate(entry, NOVA_PROJECT_CONTEXT)
    }

    if not eligible:
        _cache_set(key, [])
        return []

    scored = _local_score(query, eligible)
    top = scored[:top_k]

    results = []
    for shard_id, score in top:
        entry = eligible[shard_id]
        meta = entry.get("meta", {})
        # Prefer the Step 1 50-token summary; fall back to guiding question excerpt
        summary = meta.get("summary", "") or entry.get("guiding_question", "")[:200]
        results.append({
            "shard_id": shard_id,
            "summary": summary,
            "confidence": round(entry.get("confidence", 1.0), 4),
            "score": round(score, 4),
            "guiding_question": entry.get("guiding_question", ""),
        })

    _cache_set(key, results)

    for r in results:
        log_shard_access(r["shard_id"], "hook_recall")

    return results
