"""
embedding_integrity.py — VectorPin-style HMAC-SHA256 signing for NOVA shard embeddings.

Generates a cryptographic signature over each embedding vector at ingestion/enrichment
time and verifies the signature before MUNINN processes the vector. Tampered or poisoned
embeddings are excluded from cosine reranking and logged as adversarial events.

Configuration:
    NOVA_EMBEDDING_HMAC_KEY  — hex or UTF-8 secret for HMAC-SHA256.
                                If unset, signing and verification are skipped silently
                                (backward-compatible with existing deployments).

Signing algorithm:
    struct.pack("<384f", *vector) → little-endian float32 bytes → hmac.new(key, msg, sha256).hexdigest()

Storage:
    JSON shards:  shard_data["context"]["embedding_sig"] = "<64-char hex>"
    MD shards:    embedding_sig: <64-char hex>  (YAML frontmatter field)
    Arrow cache:  embedding_sig column (nullable string)

Verification happens during Arrow cache build (amortized, not per-retrieval).
The NÓTT SCHEDULED pass runs a sweep of all shards to catch any that were
mutated after the last cache build.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import struct
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

EMBEDDING_SIG_FIELD = "embedding_sig"
EMBEDDING_DIM = 384

# Pack format for a 384-element float32 vector, little-endian.
_PACK_FMT = f"<{EMBEDDING_DIM}f"


def get_hmac_key() -> bytes | None:
    """Return the HMAC key bytes from env, or None if the key is not configured."""
    import os
    raw = os.environ.get("NOVA_EMBEDDING_HMAC_KEY", "").strip()
    if not raw:
        return None
    # Accept hex-encoded key (64 hex chars = 32 bytes) or raw UTF-8.
    try:
        if len(raw) == 64 and all(c in "0123456789abcdefABCDEF" for c in raw):
            return bytes.fromhex(raw)
    except ValueError:
        pass
    return raw.encode("utf-8")


def sign_embedding(vector: list[float]) -> str | None:
    """
    Sign an embedding vector with HMAC-SHA256.

    Returns a 64-character hex digest, or None if NOVA_EMBEDDING_HMAC_KEY is unset.
    The vector is serialised as a little-endian float32 array before signing.
    """
    key = get_hmac_key()
    if key is None:
        return None

    if not vector or len(vector) != EMBEDDING_DIM:
        logger.warning(
            "embedding_integrity: unexpected vector length %d (expected %d); skipping sign",
            len(vector) if vector else 0, EMBEDDING_DIM,
        )
        return None

    try:
        msg = struct.pack(_PACK_FMT, *vector)
        return hmac.new(key, msg, hashlib.sha256).hexdigest()
    except Exception as exc:
        logger.warning("embedding_integrity: sign failed: %s", exc)
        return None


def verify_embedding(vector: list[float], signature: str) -> bool:
    """
    Verify that *signature* matches the HMAC-SHA256 of *vector*.

    Returns True when:
    - NOVA_EMBEDDING_HMAC_KEY is unset  (skip: no key configured)
    - The computed digest matches *signature* (timing-safe compare)

    Returns False when:
    - The digests differ  (tampered/replaced vector)
    """
    key = get_hmac_key()
    if key is None:
        return True  # integrity checking disabled; accept everything

    if not vector or len(vector) != EMBEDDING_DIM:
        return False

    try:
        msg = struct.pack(_PACK_FMT, *vector)
        computed = hmac.new(key, msg, hashlib.sha256).hexdigest()
        return hmac.compare_digest(computed, signature.lower())
    except Exception as exc:
        logger.warning("embedding_integrity: verify failed: %s", exc)
        return False


def _get_log_path() -> str:
    """Return the embedding_integrity.jsonl log path from config."""
    try:
        from config import EMBEDDING_INTEGRITY_LOG
        return EMBEDDING_INTEGRITY_LOG
    except ImportError:
        return str(Path(__file__).parent.parent / "embedding_integrity.jsonl")


def log_integrity_failure(
    shard_id: str,
    stored_sig: str | None,
    computed_sig: str | None,
    detected_at: str,
    query: str | None = None,
    action: str = "excluded_from_retrieval",
) -> None:
    """
    Append a structured JSONL record to embedding_integrity.jsonl.

    Arguments:
        shard_id     — the shard whose embedding failed verification
        stored_sig   — what was persisted in the shard (may be None)
        computed_sig — what we recomputed from the vector
        detected_at  — "arrow_cache_build" | "nott_scan"
        query        — retrieval query that triggered the check (optional)
        action       — disposition taken ("excluded_from_retrieval" | "flagged_only")
    """
    record = {
        "event_type": "embedding_integrity_failure",
        "shard_id": shard_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "detected_at": detected_at,
        "stored_sig": stored_sig,
        "computed_sig": computed_sig,
        "vector_length": EMBEDDING_DIM,
        "query": query,
        "action": action,
    }
    log_path = _get_log_path()
    try:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception as exc:
        logger.warning("embedding_integrity: could not write failure log: %s", exc)

    logger.warning(
        "EMBEDDING INTEGRITY FAILURE: shard=%s detected_at=%s stored=%s… computed=%s…",
        shard_id,
        detected_at,
        (stored_sig or "")[:16],
        (computed_sig or "")[:16],
    )
