"""
ravens.py — HUGINN and MUNINN retrieval agents for NOVA

HUGINN (Haiku) — Fast retrieval messenger.
  Odin's raven of Thought. Flies first on every query, returns quickly
  with candidate shard IDs and confidence scores. If confidence exceeds
  HUGINN_CONFIDENCE_THRESHOLD, MUNINN is never invoked.

MUNINN (Sonnet) — Deep memory retrieval.
  Odin's raven of Memory. Invoked only when HUGINN is not confident enough.
  Re-ranks candidates using semantic judgment: embedding similarity against
  the query vector, with shard content and graph context as tiebreakers.

MIMIR (your laptop) hosts the well. Both ravens drink from it.

Two-pass design:
  HUGINN.retrieve()  →  token-overlap pre-filter, then Haiku LLM re-score
  MUNINN.rerank()    →  cosine re-rank over HUGINN candidates, then Sonnet deep rerank

Both passes fall back to local-only (no API call) when CLAUDE_API_KEY is absent:
  - HUGINN local: token-overlap + Jaccard blend × confidence × trust
  - MUNINN local: query-embedding cosine similarity

Usage tracking:
  All operations log to nova_usage.jsonl with operator="HUGINN" or "MUNINN".
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from collections import Counter

import anthropic

logger = logging.getLogger(__name__)
_error_counts: Counter[str] = Counter()


def _record_error(operation: str, exc: Exception) -> None:
    _error_counts[operation] += 1
    logger.warning("ravens.%s failed (%s): %s", operation, type(exc).__name__, exc)

# Timeout (seconds) for each LLM API call.  Falls back to local scores on expiry.
_RAVEN_API_TIMEOUT = float(os.environ.get("RAVEN_API_TIMEOUT", "10"))
_RAVEN_LOG_QUERY_PREVIEW = os.environ.get("NOVA_LOG_QUERY_PREVIEW", "0").lower() in {
    "1",
    "true",
    "yes",
    "on",
}


# ═══════════════════════════════════════════════════════════
# XML SCORE PARSER
# ═══════════════════════════════════════════════════════════

_SCORE_RE = re.compile(
    r'<score\s+id="([^"]+)"\s+value="([^"]+)">(.*?)</score>',
    re.DOTALL,
)


def _parse_score_xml(raw: str) -> tuple[dict, dict]:
    """
    Parse XML-tagged LLM score output into (scores, reasoning) dicts.

    Expected format per shard:
        <score id="<shard_id>" value="<float 0-1>">reason text</score>

    Tolerates extra whitespace, multi-line reasoning, and partial output.
    Defaults to value=0.5 on any parse failure for a given tag.
    """
    scores: dict[str, float] = {}
    reasoning: dict[str, str] = {}
    for m in _SCORE_RE.finditer(raw):
        shard_id = m.group(1).strip()
        value_str = m.group(2).strip()
        note = m.group(3).strip()
        try:
            scores[shard_id] = float(value_str)
        except ValueError:
            scores[shard_id] = 0.5
        reasoning[shard_id] = note or "xml-scored"
    return scores, reasoning


def _query_log_metadata(query: str) -> dict:
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:12]
    payload = {
        "query_length": len(query),
        "query_sha256_12": digest,
    }
    if _RAVEN_LOG_QUERY_PREVIEW:
        payload["query_preview"] = query[:80]
    return payload


# ═══════════════════════════════════════════════════════════
# SHARED TYPES
# ═══════════════════════════════════════════════════════════

@dataclass
class RetrievalResult:
    """Result from a HUGINN or MUNINN retrieval pass."""
    shard_ids: list[str]
    scores: dict[str, float]                  # shard_id → weighted score
    reasoning: dict[str, str]                 # shard_id → brief reasoning note
    used_llm: bool                            # True if an LLM was invoked
    max_confidence: float                     # highest weighted score in this result
    operator: str = "HUGINN"                  # "HUGINN" or "MUNINN"

    def is_confident(self, threshold: float) -> bool:
        return self.max_confidence >= threshold

    def as_log_metadata(self) -> dict:
        return {
            "operator": self.operator,
            "used_llm": self.used_llm,
            "max_confidence": round(self.max_confidence, 4),
            "shard_count": len(self.shard_ids),
        }


# ═══════════════════════════════════════════════════════════
# HUGINN — Fast retrieval messenger
# ═══════════════════════════════════════════════════════════

class Huginn:
    """
    Odin's raven of Thought. Flies fast, returns quickly.

    Local pre-filter (token-overlap + Jaccard) then Haiku LLM re-score.
    Falls back to local-only when CLAUDE_API_KEY is absent.
    """

    def __init__(
        self,
        shard_dir: str,
        usage_log_file: str,
        confidence_threshold: float = 0.7,
    ):
        self.shard_dir = shard_dir
        self.usage_log_file = usage_log_file
        self.confidence_threshold = confidence_threshold

    async def retrieve(
        self,
        query: str,
        index: dict,
        top_n: int = 5,
    ) -> RetrievalResult:
        """
        First-pass retrieval. Runs on every query.

        Returns RetrievalResult. If result.is_confident(threshold) is True,
        the caller should skip MUNINN entirely.
        """
        from config import CLAUDE_API_KEY, HUGINN_MODEL

        # ── Local pre-filter ──────────────────────────────────────────────
        scored = self._local_retrieve(query, index, top_n * 3)
        if not scored:
            result = RetrievalResult(
                shard_ids=[], scores={}, reasoning={},
                used_llm=False, max_confidence=0.0, operator="HUGINN",
            )
            self._log(query, result)
            return result

        used_llm = False
        shard_ids = [s[0] for s in scored[:top_n]]
        scores = {s[0]: round(s[1], 4) for s in scored[:top_n]}
        reasoning = {sid: "local: token-overlap + Jaccard blend × confidence × trust" for sid in shard_ids}

        # ── Haiku LLM re-score (with timeout guard) ─────────────────────
        if CLAUDE_API_KEY:
            try:
                summaries = [
                    {
                        "id": sid,
                        "question": index.get(sid, {}).get("guiding_question", ""),
                        "summary": index.get(sid, {}).get("context_summary", ""),
                        "confidence": index.get(sid, {}).get("confidence", 1.0),
                        "local_score": round(s[1], 4),
                    }
                    for sid, s in [(x[0], x) for x in scored]
                ]
                prompt = (
                    f"Query: {query}\n\n"
                    "Below are candidate memory shards. Score each from 0.0 to 1.0 for relevance "
                    "to the query. Return ONLY XML score tags, one per shard:\n"
                    '<score id="<shard_id>" value="<float 0-1>">brief reason</score>\n\n'
                    f"Shards:\n{json.dumps(summaries, indent=2)}"
                )

                def _huginn_api_call() -> str:
                    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
                    response = client.messages.create(
                        model=HUGINN_MODEL,
                        max_tokens=512,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    return response.content[0].text.strip()

                raw = await asyncio.wait_for(
                    asyncio.to_thread(_huginn_api_call),
                    timeout=_RAVEN_API_TIMEOUT,
                )
                llm_scores, llm_reasoning = _parse_score_xml(raw)
                if llm_scores:
                    sorted_ids = sorted(llm_scores, key=lambda k: llm_scores[k], reverse=True)[:top_n]
                    shard_ids = sorted_ids
                    scores = {sid: round(float(llm_scores[sid]), 4) for sid in sorted_ids}
                    reasoning = {sid: llm_reasoning.get(sid, "haiku-scored") for sid in sorted_ids}
                    used_llm = True
            except asyncio.TimeoutError:
                logger.warning("HUGINN: Haiku API call timed out after %.0fs — using local scores", _RAVEN_API_TIMEOUT)
            except Exception as exc:
                _record_error("huginn_llm_rescore", exc)

        max_conf = max(scores.values()) if scores else 0.0
        result = RetrievalResult(
            shard_ids=shard_ids,
            scores=scores,
            reasoning=reasoning,
            used_llm=used_llm,
            max_confidence=max_conf,
            operator="HUGINN",
        )
        self._log(query, result)
        return result

    # ── Local fallback ────────────────────────────────────────────────────
    # FALLBACK: local token-overlap, called by retrieve() when LLM unavailable
    # ──────────────────────────────────────────────────────────────────────

    def _local_retrieve(self, query: str, index: dict, top_n: int) -> list[tuple[str, float]]:
        """
        Token-overlap + Jaccard blend × confidence × trust weighting.
        Jaccard borrowed from hermes-agent holographic/retrieval.py.
        trust_score defaults to 1.0 when not present in the index entry.
        """
        scored = []
        msg_lower = query.lower()
        msg_tokens = set(msg_lower.split())

        for shard_id, entry in index.items():
            tags = entry.get("tags", [])
            if "archived" in tags or "forgotten" in tags:
                continue

            confidence = entry.get("confidence", 1.0)
            # trust_score: boosted by access frequency, reduced on low-confidence updates
            trust = entry.get("trust_score", 1.0)

            searchable = " ".join([
                entry.get("guiding_question", ""),
                entry.get("context_summary", ""),
                " ".join(entry.get("context_topics", [])),
                entry.get("meta", {}).get("theme", ""),
                entry.get("meta", {}).get("intent", ""),
            ]).lower()

            search_tokens = set(searchable.split())
            overlap = msg_tokens & search_tokens
            union = msg_tokens | search_tokens

            # Precision: overlap / query length (short-query biased)
            base_score = len(overlap) / max(len(msg_tokens), 1)
            # Jaccard: overlap / union (symmetric, penalises verbose shards)
            jaccard = len(overlap) / max(len(union), 1)
            # Blend 60/40, then scale by confidence × trust
            blended = (0.6 * base_score + 0.4 * jaccard) * confidence * trust

            if blended > 0.02:
                scored.append((shard_id, blended))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_n]

    def _log(self, query: str, result: RetrievalResult):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "tool": "retrieve",
            "operator": "HUGINN",
            "shards": result.shard_ids,
            "metadata": {
                **result.as_log_metadata(),
                **_query_log_metadata(query),
            },
        }
        try:
            with open(self.usage_log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as exc:
            _record_error("huginn_log_write", exc)


# ═══════════════════════════════════════════════════════════
# MUNINN — Deep memory retrieval
# ═══════════════════════════════════════════════════════════

class Muninn:
    """
    Odin's raven of Memory. Slower, harder to call back, more important.

    Re-ranks HUGINN candidates using Sonnet for semantic judgment; falls back
    to query-embedding cosine similarity when CLAUDE_API_KEY is absent.
    """

    def __init__(
        self,
        shard_dir: str,
        usage_log_file: str,
    ):
        self.shard_dir = shard_dir
        self.usage_log_file = usage_log_file

    async def rerank(
        self,
        query: str,
        candidates: RetrievalResult,
        index: dict,
        top_n: int = 5,
    ) -> RetrievalResult:
        """
        Second-pass re-ranking. Only invoked when HUGINN is not confident.

        Returns a new RetrievalResult with re-ranked shard_ids.
        Falls back to passing HUGINN result through if no embeddings available.
        """
        from config import CLAUDE_API_KEY, MUNINN_MODEL

        reranked = await asyncio.to_thread(self._local_rerank, query, candidates, index, top_n)
        used_llm = False
        shard_ids = reranked["shard_ids"]
        scores = reranked["scores"]
        reasoning = reranked["reasoning"]

        # ── Sonnet LLM deep rerank (with timeout guard) ───────────────────
        if CLAUDE_API_KEY and candidates.shard_ids:
            try:
                shard_blobs = []
                for sid in candidates.shard_ids:
                    shard_path = Path(self.shard_dir) / (sid + ".json")
                    entry = index.get(sid, {})
                    turns_preview = ""
                    try:
                        with open(shard_path, "r", encoding="utf-8") as f:
                            shard_data = json.load(f)
                        turns = shard_data.get("conversation_history", [])
                        turns_preview = " | ".join(
                            f"{t.get('user', '')} → {t.get('ai', '')}"[:120]
                            for t in turns[-3:]
                        )
                    except Exception as exc:
                        _record_error("muninn_recent_turns_read", exc)
                    shard_blobs.append({
                        "id": sid,
                        "question": entry.get("guiding_question", ""),
                        "summary": entry.get("context_summary", ""),
                        "topics": entry.get("context_topics", []),
                        "confidence": entry.get("confidence", 1.0),
                        "huginn_score": round(candidates.scores.get(sid, 0.0), 4),
                        "local_rerank_score": round(scores.get(sid, 0.0), 4),
                        "recent_turns": turns_preview,
                    })

                prompt = (
                    f"Query: {query}\n\n"
                    "Re-rank these memory shards by relevance to the query. "
                    "Consider the shard summary, topics, confidence, and recent content. "
                    "Return ONLY XML score tags, one per shard:\n"
                    '<score id="<shard_id>" value="<float 0-1>">brief reason</score>\n\n'
                    f"Shards:\n{json.dumps(shard_blobs, indent=2)}"
                )

                def _muninn_api_call() -> str:
                    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
                    response = client.messages.create(
                        model=MUNINN_MODEL,
                        max_tokens=1024,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    return response.content[0].text.strip()

                raw = await asyncio.wait_for(
                    asyncio.to_thread(_muninn_api_call),
                    timeout=_RAVEN_API_TIMEOUT,
                )
                llm_scores, llm_reasoning = _parse_score_xml(raw)
                if llm_scores:
                    sorted_ids = sorted(llm_scores, key=lambda k: llm_scores[k], reverse=True)[:top_n]
                    shard_ids = sorted_ids
                    scores = {sid: round(float(llm_scores[sid]), 4) for sid in sorted_ids}
                    reasoning = {sid: llm_reasoning.get(sid, "sonnet-reranked") for sid in sorted_ids}
                    used_llm = True
            except asyncio.TimeoutError:
                logger.warning("MUNINN: Sonnet API call timed out after %.0fs — using local rerank scores", _RAVEN_API_TIMEOUT)
            except Exception as exc:
                _record_error("muninn_llm_rerank", exc)

        result = RetrievalResult(
            shard_ids=shard_ids,
            scores=scores,
            reasoning=reasoning,
            used_llm=used_llm,
            max_confidence=max(scores.values()) if scores else 0.0,
            operator="MUNINN",
        )
        self._log(query, candidates, result)
        return result

    # ── Local fallback ────────────────────────────────────────────────────
    # FALLBACK: query-embedding cosine re-rank, used by rerank() when LLM unavailable.
    # First time NOVA does query-to-shard semantic comparison.
    # ──────────────────────────────────────────────────────────────────────

    def _local_rerank(
        self,
        query: str,
        candidates: RetrievalResult,
        index: dict,
        top_n: int,
    ) -> dict:
        """
        Generate query embedding, cosine-compare against candidate shard embeddings.
        Falls back to passthrough if embeddings unavailable.
        """
        from nova_embeddings_local import generate_local_embedding

        query_embedding = generate_local_embedding(query)

        if query_embedding is None or not candidates.shard_ids:
            # No embedding model — passthrough unchanged
            return {
                "shard_ids": candidates.shard_ids[:top_n],
                "scores": {sid: candidates.scores.get(sid, 0.0) for sid in candidates.shard_ids[:top_n]},
                "reasoning": {sid: "passthrough (no embedding available)" for sid in candidates.shard_ids[:top_n]},
            }

        rescored = []
        for shard_id in candidates.shard_ids:
            shard_path = Path(self.shard_dir) / (shard_id + ".json")
            shard_embedding = None
            try:
                with open(shard_path, "r", encoding="utf-8") as f:
                    shard_data = json.load(f)
                shard_embedding = shard_data.get("context", {}).get("embedding")
            except Exception as exc:
                _record_error("muninn_local_rerank_read", exc)

            if shard_embedding:
                sim = _cosine(query_embedding, shard_embedding)
                # Blend cosine sim with original HUGINN confidence weight
                huginn_score = candidates.scores.get(shard_id, 0.0)
                blended = 0.6 * sim + 0.4 * huginn_score
                rescored.append((shard_id, blended, f"cosine={sim:.3f} huginn={huginn_score:.3f}"))
            else:
                # No embedding on this shard — use HUGINN score but penalise slightly
                huginn_score = candidates.scores.get(shard_id, 0.0)
                rescored.append((shard_id, huginn_score * 0.8, "no shard embedding, huginn score penalised"))

        rescored.sort(key=lambda x: x[1], reverse=True)
        top = rescored[:top_n]

        return {
            "shard_ids": [s[0] for s in top],
            "scores": {s[0]: round(s[1], 4) for s in top},
            "reasoning": {s[0]: s[2] for s in top},
        }

    def _log(self, query: str, huginn_result: RetrievalResult, result: RetrievalResult):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "tool": "rerank",
            "operator": "MUNINN",
            "shards": result.shard_ids,
            "metadata": {
                **result.as_log_metadata(),
                **_query_log_metadata(query),
                "huginn_candidates": huginn_result.shard_ids,
                "huginn_max_confidence": round(huginn_result.max_confidence, 4),
            },
        }
        try:
            with open(self.usage_log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as exc:
            _record_error("muninn_log_write", exc)


# ═══════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════

def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two embedding vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
