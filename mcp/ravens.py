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
  HUGINN.retrieve()  →  fast token-overlap + confidence weighting
  MUNINN.rerank()    →  query-embedding cosine re-rank over HUGINN candidates

LLM dispatch is stub-ready — stubs are marked PHASE 5.
Until then, local-only fallbacks provide genuine improvement:
  - HUGINN local: token-overlap + confidence weighting (same as before)
  - MUNINN local: query-embedding cosine similarity (NEW — first time the
    system does query-to-shard semantic comparison rather than shard-to-shard)

Usage tracking:
  All operations log to nova_usage.jsonl with operator="HUGINN" or "MUNINN".
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


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

    Uses token-overlap + confidence weighting as the local pass.
    LLM stub (Haiku) is marked PHASE 5 and not invoked.
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
        # ── PHASE 5: wire Haiku LLM call here ────────────────────────────
        # When enabled: send query + index summaries to claude-haiku-3-5,
        # parse JSON response: {shard_id: score, reasoning: {shard_id: note}}.
        # Set used_llm=True and populate reasoning from LLM response.
        # ──────────────────────────────────────────────────────────────────

        scored = self._local_retrieve(query, index, top_n)
        shard_ids = [s[0] for s in scored]
        scores = {s[0]: round(s[1], 4) for s in scored}
        reasoning = {sid: "local: token-overlap + Jaccard blend × confidence × trust" for sid in shard_ids}
        max_conf = max(scores.values()) if scores else 0.0

        result = RetrievalResult(
            shard_ids=shard_ids,
            scores=scores,
            reasoning=reasoning,
            used_llm=False,
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
                "query_preview": query[:80],
            },
        }
        try:
            with open(self.usage_log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════
# MUNINN — Deep memory retrieval
# ═══════════════════════════════════════════════════════════

class Muninn:
    """
    Odin's raven of Memory. Slower, harder to call back, more important.

    Re-ranks HUGINN candidates using query-embedding cosine similarity.
    This is the first time the NOVA system does query-to-shard semantic
    comparison (previously only shard-to-shard for merge detection).

    LLM stub (Sonnet) is marked PHASE 5 and not invoked.
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
        # ── PHASE 5: wire Sonnet LLM call here ───────────────────────────
        # When enabled: send query + full shard summaries (from index + shard
        # data) to claude-sonnet-4-5, parse JSON ranking response.
        # Consider: shard content, confidence scores, decay state, graph rels.
        # Set used_llm=True and populate reasoning from LLM response.
        # ──────────────────────────────────────────────────────────────────

        reranked = self._local_rerank(query, candidates, index, top_n)
        result = RetrievalResult(
            shard_ids=reranked["shard_ids"],
            scores=reranked["scores"],
            reasoning=reranked["reasoning"],
            used_llm=False,
            max_confidence=max(reranked["scores"].values()) if reranked["scores"] else 0.0,
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
            except Exception:
                pass

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
                "query_preview": query[:80],
                "huginn_candidates": huginn_result.shard_ids,
                "huginn_max_confidence": round(huginn_result.max_confidence, 4),
            },
        }
        try:
            with open(self.usage_log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass


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
