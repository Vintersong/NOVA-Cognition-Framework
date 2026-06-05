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
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from collections import Counter
from typing import Any

import anthropic
from config import parse_bool_env, NOVA_AGENT_INFERENCE_WEIGHT, NOVA_PROJECT_CONTEXT, QUARANTINE_PENALTY
from store import passes_state_gate

try:
    from nova_hotpool import lookup as _hp_lookup, insert as _hp_insert
    _HOTPOOL_AVAILABLE = True
except ImportError:
    _hp_lookup = None
    _hp_insert = None
    _HOTPOOL_AVAILABLE = False

logger = logging.getLogger(__name__)
_error_counts: Counter[str] = Counter()


def _record_error(operation: str, exc: Exception) -> None:
    _error_counts[operation] += 1
    logger.warning("ravens.%s failed (%s): %s", operation, type(exc).__name__, exc)


# ═══════════════════════════════════════════════════════════
# BENCH JSONL HELPER  (NOVA_BENCH=1)
# ═══════════════════════════════════════════════════════════
#
# Field set mirrors tests/bench_corpus.py BenchTimer.__exit__ so every
# JSONL row has a uniform key shape — defensive against report logic
# that uses direct key access. Unknown stages emit None for fields they
# don't compute.

def _emit_bench_row(
    log_path: str,
    corpus_size: int | None,
    query_id: str | None,
    stage: str,
    duration_ms: float,
    *,
    candidates_in: int | None = None,
    candidates_out: int | None = None,
    pre_activation_ids: list[str] | None = None,
    post_activation_ids: list[str] | None = None,
) -> None:
    rec = {
        "ts": datetime.now().isoformat(),
        "corpus_size": corpus_size,
        "stage": stage,
        "query_id": query_id,
        "duration_ms": duration_ms,
        "candidates_in": candidates_in,
        "candidates_out": candidates_out,
        "recall_at_5": None,
        "recall_at_10": None,
        "huginn_called": None,
        "muninn_called": None,
        "muninn_candidates": None,
        "huginn_max_confidence": None,
        "confidence_threshold": None,
        "muninn_triggered": None,
        "pre_activation_ids": pre_activation_ids,
        "post_activation_ids": post_activation_ids,
        "pre_activation_recall_at_5": None,
        "pre_activation_recall_at_10": None,
        "in_live_pipeline": True,
    }
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")

# Timeout (seconds) for each LLM API call.  Falls back to local scores on expiry.
_RAVEN_API_TIMEOUT = float(os.environ.get("RAVEN_API_TIMEOUT", "10"))
_ENABLE_QUERY_PREVIEW = parse_bool_env("NOVA_LOG_QUERY_PREVIEW", default=False)


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


def _query_log_metadata(query: str) -> dict[str, Any]:
    """Build privacy-preserving query log metadata (digest/length; preview opt-in)."""
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
    payload = {
        "query_length": len(query),
        "query_sha256_16": digest,
    }
    if _ENABLE_QUERY_PREVIEW:
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
                    import httpx
                    client = anthropic.Anthropic(
                        api_key=CLAUDE_API_KEY,
                        timeout=httpx.Timeout(_RAVEN_API_TIMEOUT, connect=5.0),
                        max_retries=0,
                    )
                    response = client.messages.create(
                        model=HUGINN_MODEL,
                        max_tokens=512,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    return response.content[0].text.strip()

                # asyncio timeout is _RAVEN_API_TIMEOUT + 3 so httpx (set to
                # _RAVEN_API_TIMEOUT) always fires first and exits the thread
                # cleanly — avoids the asyncio cancel / hung-thread race.
                raw = await asyncio.wait_for(
                    asyncio.to_thread(_huginn_api_call),
                    timeout=_RAVEN_API_TIMEOUT + 3.0,
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

        if _HOTPOOL_AVAILABLE and shard_ids:
            for sid in shard_ids:
                _entry = index.get(sid, {})
                _hp_insert(
                    sid,
                    _entry.get("confidence", 1.0),
                    _entry.get("meta", {}).get("intent", "reflection"),
                )

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
            if not passes_state_gate(entry, NOVA_PROJECT_CONTEXT):
                continue

            cached_conf = _hp_lookup(shard_id) if _HOTPOOL_AVAILABLE else None
            confidence = cached_conf if cached_conf is not None else entry.get("confidence", 1.0)
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

            # Deprioritise agent-inferred shards relative to external sources
            if entry.get("meta", {}).get("source", "agent_inference") == "agent_inference":
                blended *= NOVA_AGENT_INFERENCE_WEIGHT

            # Penalise shards still in quarantine window
            quarantine_until = entry.get("meta", {}).get("quarantine_until")
            if quarantine_until:
                try:
                    from datetime import datetime as _dt
                    if _dt.fromisoformat(quarantine_until) > _dt.now():
                        blended *= QUARANTINE_PENALTY
                except (ValueError, TypeError):
                    pass

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

        try:
            reranked = await asyncio.wait_for(
                asyncio.to_thread(self._local_rerank, query, candidates, index, top_n),
                timeout=_RAVEN_API_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("MUNINN: local rerank timed out — passing through HUGINN result")
            reranked = {
                "shard_ids": candidates.shard_ids[:top_n],
                "scores": {sid: candidates.scores.get(sid, 0.5) for sid in candidates.shard_ids[:top_n]},
                "reasoning": {sid: "local: passthrough (timeout)" for sid in candidates.shard_ids[:top_n]},
            }
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
                    import httpx
                    client = anthropic.Anthropic(
                        api_key=CLAUDE_API_KEY,
                        timeout=httpx.Timeout(_RAVEN_API_TIMEOUT, connect=5.0),
                        max_retries=0,
                    )
                    response = client.messages.create(
                        model=MUNINN_MODEL,
                        max_tokens=1024,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    return response.content[0].text.strip()

                # asyncio timeout is _RAVEN_API_TIMEOUT + 3 so httpx (set to
                # _RAVEN_API_TIMEOUT) always fires first and exits the thread
                # cleanly — avoids the asyncio cancel / hung-thread race.
                raw = await asyncio.wait_for(
                    asyncio.to_thread(_muninn_api_call),
                    timeout=_RAVEN_API_TIMEOUT + 3.0,
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

        # ── Spreading activation — optional third pass ────────────────────────
        # Propagates MUNINN scores through the knowledge graph to surface
        # related shards not in the original HUGINN candidate set.
        # Skipped gracefully when the graph is sparse or unavailable.
        _bench_on = os.environ.get("NOVA_BENCH") == "1"
        _bench_t0 = time.perf_counter() if _bench_on else None
        _bench_in = len(result.shard_ids)
        # Snapshot pre-activation top-N for the bench's recall-delta probe.
        _pre_activation_ids = list(result.shard_ids[:top_n]) if _bench_on else None

        # Sub-stage timers — each None unless NOVA_BENCH=1.
        _t_graph_load: float | None = None
        _t_community: float | None = None
        _t_bfs: float | None = None
        _graph_edges = 0
        _activation_ran = False
        try:
            from graph import load_graph
            from clustering import detect_communities
            from spreading_activation import spreading_activation as _spread
            from config import NOVA_ACTIVATION_MIN_EDGES

            _sub_t = time.perf_counter() if _bench_on else 0.0
            _graph = load_graph()
            _graph_edges = len(_graph.get("relations", []))
            if _bench_on:
                _t_graph_load = (time.perf_counter() - _sub_t) * 1000
            if _graph_edges >= NOVA_ACTIVATION_MIN_EDGES:
                _sub_t = time.perf_counter() if _bench_on else 0.0
                _cluster_map = detect_communities(_graph)
                if _bench_on:
                    _t_community = (time.perf_counter() - _sub_t) * 1000

                _sub_t = time.perf_counter() if _bench_on else 0.0
                _activation = _spread(
                    seeds=result.scores,
                    graph=_graph,
                    cluster_map=_cluster_map,
                )
                if _bench_on:
                    _t_bfs = (time.perf_counter() - _sub_t) * 1000
                _activation_ran = True

                if _activation:
                    # Merge: 0.7 × original MUNINN score + 0.3 × activation score
                    _all_ids = set(result.shard_ids) | set(_activation)
                    _merged = {
                        sid: round(
                            0.7 * result.scores.get(sid, 0.0)
                            + 0.3 * _activation.get(sid, 0.0),
                            4,
                        )
                        for sid in _all_ids
                    }
                    _sorted = sorted(
                        _merged, key=_merged.__getitem__, reverse=True
                    )[:top_n]
                    _reasoning = dict(result.reasoning)
                    for sid in _sorted:
                        act_val = _activation.get(sid, 0.0)
                        if act_val > 0:
                            suffix = f" +act={round(act_val, 4)}"
                            _reasoning[sid] = _reasoning.get(sid, "activation") + suffix
                    result = RetrievalResult(
                        shard_ids=_sorted,
                        scores={sid: _merged[sid] for sid in _sorted},
                        reasoning=_reasoning,
                        used_llm=result.used_llm,
                        max_confidence=max(_merged[s] for s in _sorted) if _sorted else 0.0,
                        operator="MUNINN",
                    )
        except Exception as exc:
            _record_error("muninn_spreading_activation", exc)
        if _bench_on:
            _bench_log = os.environ.get("NOVA_BENCH_LOG")
            if _bench_log:
                try:
                    _bench_size = os.environ.get("NOVA_BENCH_CORPUS_SIZE")
                    _bench_size_i = int(_bench_size) if _bench_size else None
                    _query_id = os.environ.get("NOVA_BENCH_QUERY_ID")
                    _post_activation_ids = list(result.shard_ids[:top_n])
                    _emit_bench_row(
                        _bench_log,
                        _bench_size_i,
                        _query_id,
                        "spreading_activation_inner",
                        (time.perf_counter() - _bench_t0) * 1000,
                        candidates_in=_bench_in,
                        candidates_out=len(result.shard_ids),
                        pre_activation_ids=_pre_activation_ids,
                        post_activation_ids=_post_activation_ids,
                    )
                    # Sub-stage rows. Community + BFS only emitted when the
                    # min-edges gate passed; graph_load always emits because
                    # it runs unconditionally.
                    if _t_graph_load is not None:
                        _emit_bench_row(
                            _bench_log, _bench_size_i, _query_id,
                            "spreading_graph_load", _t_graph_load,
                            candidates_in=_graph_edges,
                            candidates_out=_graph_edges,
                        )
                    if _t_community is not None:
                        _emit_bench_row(
                            _bench_log, _bench_size_i, _query_id,
                            "spreading_community_detect", _t_community,
                            candidates_in=_graph_edges,
                        )
                    if _t_bfs is not None:
                        _emit_bench_row(
                            _bench_log, _bench_size_i, _query_id,
                            "spreading_bfs", _t_bfs,
                            candidates_in=len(result.scores),
                            candidates_out=len(result.shard_ids),
                        )
                except Exception:
                    pass
        # ── End spreading activation ──────────────────────────────────────────

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
        from nova_embeddings_local import get_embedding_model_if_ready, generate_local_embedding

        # Non-blocking: skip cosine rerank if model is still loading (prewarm in progress).
        if get_embedding_model_if_ready() is None:
            return {
                "shard_ids": candidates.shard_ids[:top_n],
                "scores": {sid: candidates.scores.get(sid, 0.5) for sid in candidates.shard_ids[:top_n]},
                "reasoning": {sid: "local: passthrough (model loading)" for sid in candidates.shard_ids[:top_n]},
            }

        query_embedding = generate_local_embedding(query)

        if query_embedding is None or not candidates.shard_ids:
            # No embedding model — passthrough unchanged
            return {
                "shard_ids": candidates.shard_ids[:top_n],
                "scores": {sid: candidates.scores.get(sid, 0.0) for sid in candidates.shard_ids[:top_n]},
                "reasoning": {sid: "passthrough (no embedding available)" for sid in candidates.shard_ids[:top_n]},
            }

        # Arrow fast path: one cached load + vectorised cosine over a contiguous
        # (k, 384) numpy view. Falls through to the legacy per-shard JSON loop
        # if pyarrow isn't installed or the cache hasn't been built yet.
        try:
            from arrow_cache import ARROW_AVAILABLE, get_arrow_cache
        except Exception:
            ARROW_AVAILABLE = False
            get_arrow_cache = None  # type: ignore[assignment]

        if ARROW_AVAILABLE:
            try:
                result = get_arrow_cache().rerank_by_cosine(
                    query_embedding,
                    candidates.shard_ids,
                    candidates.scores,
                    top_n,
                )
                if result["shard_ids"]:
                    return result
            except Exception as exc:
                _record_error("muninn_local_rerank_arrow", exc)

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
