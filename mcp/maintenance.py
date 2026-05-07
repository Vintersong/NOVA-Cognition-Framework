"""
maintenance.py — Confidence decay, auto-compaction, cosine similarity,
and merge-candidate detection for NOVA.

All policy thresholds come from config.py — change an env var, change the behaviour.
These functions are injected into NÓTT as callables; do not call them directly
in tool handlers (NÓTT owns scheduling).
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime

from nova_embeddings_local import _generate_compaction_summary
from config import (
    SHARD_DIR,
    COMPACT_THRESHOLD,
    COMPACT_KEEP_RECENT,
    DECAY_RATE,
    DECAY_INTERVAL_DAYS,
    MERGE_SIMILARITY_THRESHOLD,
)


# ═══════════════════════════════════════════════════════════
# CONFIDENCE HELPERS
# ═══════════════════════════════════════════════════════════

def get_confidence(shard_data: dict) -> float:
    """Get current confidence score. Default 1.0 for shards without it."""
    return shard_data.get("meta_tags", {}).get("confidence", 1.0)


def apply_confidence_decay(shard_data: dict) -> float:
    """
    Decay confidence for shards not accessed in DECAY_INTERVAL_DAYS.
    Formula: MAX(0.1, confidence * (1 - decay_rate))
    Returns new confidence value.
    """
    meta = shard_data.setdefault("meta_tags", {})
    current_confidence = meta.get("confidence", 1.0)
    last_used_str = meta.get("last_used")

    if not last_used_str:
        return current_confidence

    try:
        last_used = datetime.fromisoformat(last_used_str)
        days_since = (datetime.now() - last_used).days

        if days_since >= DECAY_INTERVAL_DAYS:
            periods = days_since // DECAY_INTERVAL_DAYS
            new_confidence = current_confidence
            for _ in range(periods):
                new_confidence = max(0.1, new_confidence * (1.0 - DECAY_RATE))
            meta["confidence"] = round(new_confidence, 4)
            return new_confidence
    except (ValueError, TypeError):
        pass

    return current_confidence


def confidence_weighted_score(base_score: float, confidence: float) -> float:
    """Weight search relevance by confidence. High confidence + high relevance = top result."""
    return base_score * confidence


# ═══════════════════════════════════════════════════════════
# AUTO-COMPACTION
# ═══════════════════════════════════════════════════════════

def maybe_compact_shard(shard_data: dict, shard_id: str) -> bool:
    """
    If conversation_history exceeds COMPACT_THRESHOLD, auto-compact:
    - Summarize older turns using the structured template from nova_embeddings_local
    - Keep only last COMPACT_KEEP_RECENT turns in full
    Returns True if compaction happened.
    """
    history = shard_data.get("conversation_history", [])
    if len(history) < COMPACT_THRESHOLD:
        return False

    older_turns = history[:-COMPACT_KEEP_RECENT]
    recent_turns = history[-COMPACT_KEEP_RECENT:]

    compaction_summary = _generate_compaction_summary(older_turns, shard_id)

    shard_data["conversation_history"] = recent_turns
    ctx = shard_data.setdefault("context", {})
    existing_summary = ctx.get("summary", "")
    if existing_summary:
        ctx["summary"] = (
            f"{existing_summary}\n\n"
            f"[COMPACTED — {len(older_turns)} earlier turns]: {compaction_summary}"
        )
    else:
        ctx["summary"] = f"[COMPACTED — {len(older_turns)} turns]: {compaction_summary}"

    ctx["last_compacted"] = datetime.now().isoformat()
    ctx["compacted_turn_count"] = ctx.get("compacted_turn_count", 0) + len(older_turns)
    shard_data.setdefault("meta_tags", {})["last_compacted"] = datetime.now().isoformat()

    return True


# ═══════════════════════════════════════════════════════════
# COSINE SIMILARITY + MERGE SUGGESTIONS
# ═══════════════════════════════════════════════════════════

def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def find_merge_candidates(shard_id: str, shard_data: dict, index: dict) -> list[dict]:
    """
    Compare this shard's embedding against all others.
    Returns list of candidates with similarity > MERGE_SIMILARITY_THRESHOLD.
    """
    my_embedding = shard_data.get("context", {}).get("embedding")
    if not my_embedding:
        return []

    candidates = []
    for other_id, entry in index.items():
        if other_id == shard_id:
            continue
        if "archived" in entry.get("tags", []):
            continue

        other_path = os.path.join(SHARD_DIR, other_id + ".json")
        if not os.path.exists(other_path):
            continue

        try:
            with open(other_path, "r", encoding="utf-8") as f:
                other_data = json.load(f)
            other_embedding = other_data.get("context", {}).get("embedding")
            if not other_embedding:
                continue

            sim = cosine_similarity(my_embedding, other_embedding)
            if sim >= MERGE_SIMILARITY_THRESHOLD:
                candidates.append({
                    "shard_id": other_id,
                    "similarity": round(sim, 4),
                    "guiding_question": other_data.get("guiding_question", ""),
                })
        except Exception:
            continue

    candidates.sort(key=lambda x: x["similarity"], reverse=True)
    return candidates
