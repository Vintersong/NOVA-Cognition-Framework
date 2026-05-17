"""
recall.py — Hook recall and cache prewarm for NOVA.

Provides hook_recall(): synchronous, confidence-floored, summary-only retrieval
for use in Claude Code hooks (UserPromptSubmit, PostToolUse).

Provides build_prewarm_context() / prewarm_session_context(): selects top-N
high-confidence shards, assembles a summary system prompt, and fires a
max_tokens=0 Anthropic API call to write the prompt into the cache before any
real requests arrive.  Subsequent requests that send the same system string
(with cache_control) receive ~0.1× read cost instead of full write cost.

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
from timeutils import parse_iso, now_utc

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
        quarantine_until = parse_iso(entry.get("meta", {}).get("quarantine_until"))
        if quarantine_until is not None and quarantine_until > now_utc():
            blended *= QUARANTINE_PENALTY

        if blended > 0.0:
            scored.append((shard_id, blended))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


# ═══════════════════════════════════════════════════════════
# CLUSTER-AWARE TOP-K WALK
# ═══════════════════════════════════════════════════════════

def _walk_topk_with_cluster_collapse(
    scored: list[tuple[str, float]],
    eligible: dict,
    top_k: int,
) -> list[dict]:
    """
    Walk the scored shard list in rank order and build up to top_k distinct
    cluster representatives.

    The first time a cluster is encountered, the shard becomes the
    representative and gets an empty ``cluster_siblings`` list. Subsequent
    shards in the same cluster are appended to the representative's siblings
    list and do not consume a top-k slot — the walk continues until top_k
    distinct cluster representatives have been collected.

    Shards without ``cluster_id`` count as their own cluster (no collapse).

    Returns the result dict list (same shape as the legacy collapse output).
    """
    results: list[dict] = []
    seen_clusters: dict[str, int] = {}  # cluster_id -> index into results

    for i, (shard_id, score) in enumerate(scored):
        entry = eligible[shard_id]
        meta = entry.get("meta", {})
        cluster_id = meta.get("cluster_id")

        if cluster_id and cluster_id in seen_clusters:
            kept_idx = seen_clusters[cluster_id]
            results[kept_idx]["cluster_siblings"].append(shard_id)
            continue

        if len(results) >= top_k:
            if i >= 2 * top_k:
                break
            continue

        summary = meta.get("summary", "") or entry.get("guiding_question", "")[:200]
        result = {
            "shard_id": shard_id,
            "summary": summary,
            "confidence": round(entry.get("confidence", 1.0), 4),
            "score": round(score, 4),
            "guiding_question": entry.get("guiding_question", ""),
        }
        if cluster_id:
            result["cluster_id"] = cluster_id
            result["cluster_siblings"] = []
            seen_clusters[cluster_id] = len(results)
        results.append(result)

    return results


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
    results = _walk_topk_with_cluster_collapse(scored, eligible, top_k)

    _cache_set(key, results)

    for r in results:
        log_shard_access(r["shard_id"], "hook_recall")

    return results


# ═══════════════════════════════════════════════════════════
# CACHE PREWARM
# ═══════════════════════════════════════════════════════════

_PREWARM_HEADER = (
    "You are working within NOVA, a persistent shard-based cognitive memory system.\n"
    "The following are pre-loaded high-confidence memory shards for this session.\n"
    "Draw on this context before reaching for new information.\n\n"
)

_PREWARM_SHARD_TEMPLATE = (
    "---\n"
    "[SHARD: {shard_id} | confidence: {confidence} | theme: {theme}]\n"
    "Q: {guiding_question}\n"
    "Summary: {summary}\n"
)


def build_prewarm_context(
    top_n: int = 20,
    project_context: str | None = None,
    min_confidence: float = 0.6,
) -> tuple[str, list[str]]:
    """
    Select top-N shards by confidence and assemble a summary-only system prompt.

    Returns (system_prompt_str, shard_ids_included).

    The returned string is deterministic for the same shard state — pass it
    with cache_control on every real request to get cache reads after the
    initial prewarm write.
    """
    index = load_index()

    eligible = {
        sid: entry for sid, entry in index.items()
        if entry.get("confidence", 1.0) >= min_confidence
        and "archived" not in entry.get("tags", [])
        and "forgotten" not in entry.get("tags", [])
        and passes_state_gate(entry, project_context or NOVA_PROJECT_CONTEXT)
    }

    sorted_shards = sorted(
        eligible.items(),
        key=lambda kv: kv[1].get("confidence", 0.0),
        reverse=True,
    )[:top_n]

    blocks = [_PREWARM_HEADER]
    shard_ids: list[str] = []

    for shard_id, entry in sorted_shards:
        meta = entry.get("meta", {}) or entry.get("meta_tags", {})
        summary = (
            meta.get("summary", "")
            or entry.get("context_summary", "")
            or entry.get("guiding_question", "")[:200]
        )
        block = _PREWARM_SHARD_TEMPLATE.format(
            shard_id=shard_id,
            confidence=round(entry.get("confidence", 1.0), 3),
            theme=meta.get("theme", "general"),
            guiding_question=entry.get("guiding_question", "")[:200],
            summary=summary[:300],
        )
        blocks.append(block)
        shard_ids.append(shard_id)

    return "".join(blocks), shard_ids


def prewarm_session_context(
    model: str = "",
    top_n: int = 20,
    project_context: str | None = None,
    min_confidence: float = 0.6,
) -> dict:
    """
    Build the session context prompt and fire a max_tokens=0 cache-prewarm
    request against the Anthropic API.

    Returns a result dict:
        system_prompt      str   — the exact string to reuse in subsequent calls
        shard_ids          list  — shard IDs included in the prompt
        token_count        int   — estimated characters (not tokens)
        cache_write_tokens int   — cache_creation_input_tokens from the API
        skipped            bool  — True when CLAUDE_API_KEY absent or no shards
        skip_reason        str   — human-readable explanation when skipped

    The caller must pass system_prompt back into every subsequent API call
    with the same cache_control block to get cache reads:

        system=[{"type": "text", "text": result["system_prompt"],
                 "cache_control": {"type": "ephemeral"}}]
    """
    from config import CLAUDE_API_KEY, MUNINN_MODEL

    target_model = model or MUNINN_MODEL

    if not CLAUDE_API_KEY:
        return {
            "system_prompt": "",
            "shard_ids": [],
            "token_count": 0,
            "cache_write_tokens": 0,
            "skipped": True,
            "skip_reason": "CLAUDE_API_KEY not set — running in local-only mode",
        }

    system_prompt, shard_ids = build_prewarm_context(
        top_n=top_n,
        project_context=project_context,
        min_confidence=min_confidence,
    )

    if not shard_ids:
        return {
            "system_prompt": "",
            "shard_ids": [],
            "token_count": 0,
            "cache_write_tokens": 0,
            "skipped": True,
            "skip_reason": f"No shards cleared confidence floor {min_confidence}",
        }

    import anthropic
    import httpx

    client = anthropic.Anthropic(
        api_key=CLAUDE_API_KEY,
        timeout=httpx.Timeout(30.0, connect=5.0),
    )

    response = client.messages.create(
        model=target_model,
        max_tokens=0,
        system=[{
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": "warmup"}],
    )

    cache_write = getattr(response.usage, "cache_creation_input_tokens", 0)

    return {
        "system_prompt": system_prompt,
        "shard_ids": shard_ids,
        "token_count": len(system_prompt),
        "cache_write_tokens": cache_write,
        "skipped": False,
        "skip_reason": "",
    }
