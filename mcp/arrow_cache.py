"""
arrow_cache.py — In-memory Arrow projection of NOVA's JSON shard store.

The shards/*.json files remain the source of truth. This module materialises
them into a single PyArrow Table on demand so hot paths (MUNINN cosine rerank,
NÓTT decay/merge passes) can compute over a contiguous columnar buffer instead
of looping per-shard JSON reads + Python-level cosine.

Write-back is lossless by design: the Arrow path never reconstructs JSON from
the table. It uses Arrow only to identify shards needing change, then re-reads
each via store.load_shard, mutates the targeted field(s), and saves through
store.save_shard. Long-tail meta_tags fields stay on disk untouched.

When pyarrow / numpy aren't installed (or NOVA_ARROW_DISABLE is set), the module
exposes ARROW_AVAILABLE = False and callers fall through to their existing
per-shard loops.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_ARROW_DISABLED_ENV = os.environ.get("NOVA_ARROW_DISABLE", "").strip().lower() in {"1", "true", "yes", "on"}

try:
    import numpy as np
    import pyarrow as pa
    import pyarrow.compute as pc  # noqa: F401  (kept for callers that import via this module)
    ARROW_AVAILABLE = not _ARROW_DISABLED_ENV
except ImportError:
    np = None  # type: ignore[assignment]
    pa = None  # type: ignore[assignment]
    pc = None  # type: ignore[assignment]
    ARROW_AVAILABLE = False
    logger.info("arrow_cache: pyarrow/numpy not installed — legacy paths will be used.")


EMBEDDING_DIM = 384

if ARROW_AVAILABLE:
    SCHEMA = pa.schema([
        pa.field("shard_id",            pa.string(),                            nullable=False),
        pa.field("guiding_question",    pa.string(),                            nullable=True),
        pa.field("embedding",           pa.list_(pa.float32(), EMBEDDING_DIM),  nullable=True),
        pa.field("confidence",          pa.float32(),                           nullable=False),
        pa.field("last_used",           pa.timestamp("us", tz="UTC"),           nullable=True),
        pa.field("usage_count",         pa.int32(),                             nullable=False),
        pa.field("enrichment_status",   pa.string(),                            nullable=True),
        pa.field("intent",              pa.string(),                            nullable=True),
        pa.field("theme",               pa.string(),                            nullable=True),
        pa.field("topics",              pa.list_(pa.string()),                  nullable=True),
        pa.field("turn_count",          pa.int32(),                             nullable=False),
        pa.field("quarantine_until",    pa.timestamp("us", tz="UTC"),           nullable=True),
        pa.field("tags",                pa.list_(pa.string()),                  nullable=True),
        pa.field("mtime_ns",            pa.int64(),                             nullable=False),
        pa.field("meta_tags_json",      pa.string(),                            nullable=True),
        pa.field("context_extras_json", pa.string(),                            nullable=True),
        pa.field("embedding_sig",       pa.string(),                            nullable=True),
    ])
else:
    SCHEMA = None  # type: ignore[assignment]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ts_to_dt(value: Any) -> "datetime | None":
    """Coerce an ISO-8601 string (naive or aware) to tz-aware UTC datetime.

    Mirrors timeutils.parse_iso semantics so the Arrow projection sees the same
    UTC moments NÓTT and store.passes_state_gate already operate on. We re-implement
    rather than import to avoid a circular import on store -> arrow_cache.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if not isinstance(value, str):
        return None
    candidate = value.strip().replace("Z", "+00:00")
    if not candidate:
        return None
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# Cache
# ─────────────────────────────────────────────────────────────────────────────

class ArrowShardCache:
    """Lazy, thread-safe Arrow projection of the JSON shard store.

    On first access, scans every shard listed in the index and builds a Table
    plus an L2-normalised (N, 384) numpy embedding matrix for vectorised cosine.
    Subsequent accesses return the cached table until ``invalidate()`` is called
    or the index file's mtime advances.
    """

    def __init__(self, shard_dir: str, index_file: str):
        self.shard_dir = shard_dir
        self.index_file = index_file
        self._lock = threading.Lock()
        self._table = None
        self._embedding_norm = None        # (N, 384) float32, L2-normalised; zero rows for missing
        self._has_embedding = None          # (N,) bool
        self._sig_verified = None           # (N,) bool; True when sig is absent or matches
        self._integrity_failures: dict[str, dict] = {}  # shard_id → failure diagnostic
        self._shard_id_to_row: dict[str, int] = {}
        self._index_mtime_ns: int | None = None

    # ── Public API ────────────────────────────────────────────────────────

    def get_table(self, *, force_reload: bool = False):
        """Return the cached Arrow table, rebuilding if invalidated or stale."""
        if not ARROW_AVAILABLE:
            return None
        if force_reload:
            with self._lock:
                self._rebuild_locked()
                return self._table
        table = self._table
        if table is not None and self._index_mtime_unchanged():
            return table
        with self._lock:
            if self._table is None or not self._index_mtime_unchanged():
                self._rebuild_locked()
            return self._table

    def invalidate(self, shard_id: "str | None" = None) -> None:
        """Drop the cache. The next ``get_table`` call rebuilds from disk.

        ``shard_id`` is accepted for call-site clarity / future incremental
        invalidation but currently triggers a full reset — partial invalidation
        is more error-prone than a sub-second rebuild for the corpus sizes
        NOVA targets.
        """
        if not ARROW_AVAILABLE:
            return
        del shard_id
        with self._lock:
            self._table = None
            self._embedding_norm = None
            self._has_embedding = None
            self._sig_verified = None
            self._integrity_failures = {}
            self._shard_id_to_row = {}
            self._index_mtime_ns = None

    # ── Replacement helpers ───────────────────────────────────────────────

    def rerank_by_cosine(
        self,
        query_embedding,
        candidate_ids: list[str],
        huginn_scores: dict[str, float],
        top_n: int,
    ) -> dict:
        """Vectorised cosine rerank over a fixed candidate set.

        Returns the same dict shape as ravens.Muninn._local_rerank's legacy
        path: ``{"shard_ids": [...], "scores": {...}, "reasoning": {...}}``.
        Rows whose shard has no embedding fall back to ``huginn_score * 0.8``
        (matches legacy ravens.py:513).
        """
        if not ARROW_AVAILABLE:
            return {"shard_ids": [], "scores": {}, "reasoning": {}}

        self.get_table()  # ensure built
        if self._embedding_norm is None or not candidate_ids:
            return {"shard_ids": [], "scores": {}, "reasoning": {}}

        q = np.asarray(query_embedding, dtype=np.float32)
        q_norm = float(np.linalg.norm(q))
        if q_norm == 0.0:
            return {"shard_ids": [], "scores": {}, "reasoning": {}}
        q_unit = q / q_norm

        rescored: list[tuple[str, float, str]] = []
        for shard_id in candidate_ids:
            # Reject embeddings that failed signature verification at cache-build time.
            if shard_id in self._integrity_failures:
                huginn_score = float(huginn_scores.get(shard_id, 0.0))
                rescored.append((
                    shard_id,
                    huginn_score * 0.8,
                    "embedding_sig_failure: excluded from cosine rerank",
                ))
                continue

            row = self._shard_id_to_row.get(shard_id)
            if row is None or not bool(self._has_embedding[row]):
                huginn_score = float(huginn_scores.get(shard_id, 0.0))
                rescored.append((
                    shard_id,
                    huginn_score * 0.8,
                    "no shard embedding, huginn score penalised",
                ))
                continue
            sim = float(self._embedding_norm[row] @ q_unit)
            huginn_score = float(huginn_scores.get(shard_id, 0.0))
            blended = 0.6 * sim + 0.4 * huginn_score
            rescored.append((
                shard_id,
                blended,
                f"cosine={sim:.3f} huginn={huginn_score:.3f}",
            ))

        rescored.sort(key=lambda x: x[1], reverse=True)
        top = rescored[:top_n]
        return {
            "shard_ids": [s[0] for s in top],
            "scores": {s[0]: round(s[1], 4) for s in top},
            "reasoning": {s[0]: s[2] for s in top},
        }

    def decay_candidates(
        self,
        *,
        now: datetime,
        decay_rate: float,
        interval_days: int,
        kind_rates: "dict[str, float] | None" = None,
        forgotten_tag: str = "forgotten",
    ) -> list[tuple[str, float, float]]:
        """Identify shards whose confidence should be decayed.

        Vectorises the maintenance.apply_confidence_decay formula over the
        Arrow ``confidence`` / ``last_used`` / ``tags`` columns. Returns
        ``[(shard_id, old_confidence, new_confidence), ...]`` for rows where
        ``new < old`` AND ``forgotten_tag`` is not in tags.

        When ``kind_rates`` is provided, each row's ``intent`` field is used to
        look up a per-kind rate; unknown intents fall back to ``decay_rate``.

        NÓTT iterates this list and writes back via store.load_shard /
        store.save_shard so long-tail meta_tags fields are preserved.
        """
        if not ARROW_AVAILABLE:
            return []

        table = self.get_table()
        if table is None or table.num_rows == 0:
            return []

        confidence_col = np.asarray(table["confidence"].to_pylist(), dtype=np.float64)
        last_used_np = table["last_used"].to_numpy(zero_copy_only=False)
        shard_ids = table["shard_id"].to_pylist()
        tags_col = table["tags"].to_pylist()
        intent_col = table["intent"].to_pylist() if kind_rates else None

        now_naive_utc = now.astimezone(timezone.utc).replace(tzinfo=None) if now.tzinfo else now
        now_np = np.datetime64(now_naive_utc, "us")
        default_factor = 1.0 - decay_rate

        results: list[tuple[str, float, float]] = []
        for i, sid in enumerate(shard_ids):
            tags = tags_col[i] or []
            if forgotten_tag in tags:
                continue
            lu = last_used_np[i]
            if np.isnat(lu):
                continue
            delta_days = int((now_np - lu).astype("timedelta64[D]").astype(np.int64))
            if delta_days < interval_days:
                continue
            periods = delta_days // interval_days
            old_conf = float(confidence_col[i])
            new_conf = old_conf
            if kind_rates and intent_col is not None:
                row_intent = intent_col[i] or "reflection"
                eff_factor = 1.0 - kind_rates.get(row_intent, decay_rate)
            else:
                eff_factor = default_factor
            for _ in range(periods):
                new_conf = max(0.1, new_conf * eff_factor)
            if new_conf < old_conf:
                results.append((sid, old_conf, new_conf))
        return results

    def merge_candidates(
        self,
        enriched_ids: set[str],
        threshold: float,
        archived_tag: str = "archived",
    ) -> list[tuple[str, str, float, str, str]]:
        """Pairwise cosine similarity over enriched, non-archived shards.

        Single ``(M, 384) @ (384, M)`` matmul, upper-triangle threshold filter.
        Returns ``(shard_a, shard_b, sim, question_a, question_b)`` tuples
        sorted by similarity descending. Pair ordering matches the legacy
        ``tuple(sorted([a, b]))`` canonicalisation in nott._merge_pass_sync.
        """
        if not ARROW_AVAILABLE:
            return []

        table = self.get_table()
        if table is None or self._embedding_norm is None or table.num_rows == 0:
            return []

        shard_ids = table["shard_id"].to_pylist()
        guiding_questions = table["guiding_question"].to_pylist()
        tags_col = table["tags"].to_pylist()

        eligible: list[int] = []
        for i, sid in enumerate(shard_ids):
            if sid not in enriched_ids:
                continue
            if archived_tag in (tags_col[i] or []):
                continue
            if not bool(self._has_embedding[i]):
                continue
            eligible.append(i)

        if len(eligible) < 2:
            return []

        idx = np.asarray(eligible, dtype=np.int64)
        M = self._embedding_norm[idx]   # (k, 384) — already L2-normalised
        sim_matrix = M @ M.T             # (k, k)

        k = len(idx)
        iu, ju = np.triu_indices(k, k=1)
        sims = sim_matrix[iu, ju]
        mask = sims >= threshold
        if not np.any(mask):
            return []
        iu = iu[mask]
        ju = ju[mask]
        sims = sims[mask]

        pairs: list[tuple[str, str, float, str, str]] = []
        for ii, jj, sim in zip(iu.tolist(), ju.tolist(), sims.tolist()):
            a_row = int(idx[ii])
            b_row = int(idx[jj])
            sid_a = shard_ids[a_row]
            sid_b = shard_ids[b_row]
            q_a = guiding_questions[a_row] or ""
            q_b = guiding_questions[b_row] or ""
            if sid_a > sid_b:
                sid_a, sid_b = sid_b, sid_a
                q_a, q_b = q_b, q_a
            pairs.append((sid_a, sid_b, round(float(sim), 4), q_a, q_b))

        pairs.sort(key=lambda p: p[2], reverse=True)
        return pairs

    # ── Internal ──────────────────────────────────────────────────────────

    def _index_mtime_unchanged(self) -> bool:
        try:
            cur = os.stat(self.index_file).st_mtime_ns
        except FileNotFoundError:
            cur = -1
        return cur == self._index_mtime_ns

    def _rebuild_locked(self) -> None:
        """Caller must hold ``self._lock``."""
        from store import load_index  # lazy: store imports nothing from arrow_cache at module load

        index = load_index()
        try:
            self._index_mtime_ns = os.stat(self.index_file).st_mtime_ns
        except FileNotFoundError:
            self._index_mtime_ns = -1

        if not index:
            self._set_empty()
            return

        shard_ids: list[str] = []
        guiding_questions: list[str | None] = []
        embeddings: list[list[float] | None] = []
        confidences: list[float] = []
        last_used: list[datetime | None] = []
        usage_counts: list[int] = []
        enrichment_statuses: list[str | None] = []
        intents: list[str | None] = []
        themes: list[str | None] = []
        topics_col: list[list[str] | None] = []
        turn_counts: list[int] = []
        quarantines: list[datetime | None] = []
        tags_col: list[list[str] | None] = []
        mtimes: list[int] = []
        meta_tags_json: list[str | None] = []
        context_extras_json: list[str | None] = []
        embedding_sig_col: list[str | None] = []
        embedding_rows: list[Any] = []
        has_embedding: list[bool] = []
        sig_verified: list[bool] = []
        new_integrity_failures: dict[str, dict] = {}

        try:
            from embedding_integrity import verify_embedding, sign_embedding, log_integrity_failure
            _integrity_available = True
        except ImportError:
            _integrity_available = False

        shard_dir = Path(self.shard_dir)

        for shard_id, entry in index.items():
            fname = entry.get("filename") or (shard_id + ".json")
            fpath = shard_dir / fname
            try:
                stat_res = fpath.stat()
            except FileNotFoundError:
                continue
            try:
                with open(fpath, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception as exc:
                logger.debug("arrow_cache: skipping %s (%s)", fname, exc)
                continue

            meta = data.get("meta_tags") or {}
            context = data.get("context") or {}
            history = data.get("conversation_history") or []
            embedding = context.get("embedding")

            shard_ids.append(shard_id)
            guiding_questions.append(data.get("guiding_question"))

            sig = context.get("embedding_sig")
            embedding_sig_col.append(sig if isinstance(sig, str) else None)

            if isinstance(embedding, list) and len(embedding) == EMBEDDING_DIM:
                # Verify signature when present and integrity module is available.
                verified = True
                if _integrity_available and sig is not None:
                    verified = verify_embedding(embedding, sig)
                    if not verified:
                        computed = sign_embedding(embedding)
                        log_integrity_failure(
                            shard_id, sig, computed, "arrow_cache_build"
                        )
                        new_integrity_failures[shard_id] = {
                            "stored_sig": sig,
                            "computed_sig": computed,
                            "detected_at": "arrow_cache_build",
                        }

                if verified:
                    emb_array = np.asarray(embedding, dtype=np.float32)
                    embeddings.append([float(x) for x in embedding])
                    embedding_rows.append(emb_array)
                    has_embedding.append(True)
                    sig_verified.append(True)
                else:
                    # Treat corrupted embedding as missing; zero row for matrix.
                    embeddings.append(None)
                    embedding_rows.append(np.zeros(EMBEDDING_DIM, dtype=np.float32))
                    has_embedding.append(False)
                    sig_verified.append(False)
            else:
                embeddings.append(None)
                embedding_rows.append(np.zeros(EMBEDDING_DIM, dtype=np.float32))
                has_embedding.append(False)
                sig_verified.append(True)  # no embedding → nothing to verify

            confidences.append(float(meta.get("confidence", 1.0) or 1.0))
            last_used.append(_ts_to_dt(meta.get("last_used")))
            usage_counts.append(int(meta.get("usage_count", 0) or 0))
            enrichment_statuses.append(meta.get("enrichment_status"))
            intents.append(meta.get("intent"))
            themes.append(meta.get("theme"))

            topics_val = context.get("topics")
            topics_col.append(list(topics_val) if isinstance(topics_val, list) else None)

            turn_counts.append(len(history) if isinstance(history, list) else 0)
            quarantines.append(_ts_to_dt(meta.get("quarantine_until")))

            tags_val = entry.get("tags")
            tags_col.append(list(tags_val) if isinstance(tags_val, list) else None)

            mtimes.append(int(stat_res.st_mtime_ns))
            try:
                meta_tags_json.append(json.dumps(meta, default=str))
            except (TypeError, ValueError):
                meta_tags_json.append(None)
            context_extras = {
                k: v for k, v in context.items()
                if k not in {"embedding", "topics", "summary", "embedding_sig"}
            }
            try:
                context_extras_json.append(json.dumps(context_extras, default=str))
            except (TypeError, ValueError):
                context_extras_json.append(None)

        if not shard_ids:
            self._set_empty()
            return

        columns = [
            pa.array(shard_ids,             type=pa.string()),
            pa.array(guiding_questions,     type=pa.string()),
            pa.array(embeddings,            type=pa.list_(pa.float32(), EMBEDDING_DIM)),
            pa.array(confidences,           type=pa.float32()),
            pa.array(last_used,             type=pa.timestamp("us", tz="UTC")),
            pa.array(usage_counts,          type=pa.int32()),
            pa.array(enrichment_statuses,   type=pa.string()),
            pa.array(intents,               type=pa.string()),
            pa.array(themes,                type=pa.string()),
            pa.array(topics_col,            type=pa.list_(pa.string())),
            pa.array(turn_counts,           type=pa.int32()),
            pa.array(quarantines,           type=pa.timestamp("us", tz="UTC")),
            pa.array(tags_col,              type=pa.list_(pa.string())),
            pa.array(mtimes,                type=pa.int64()),
            pa.array(meta_tags_json,        type=pa.string()),
            pa.array(context_extras_json,   type=pa.string()),
            pa.array(embedding_sig_col,     type=pa.string()),
        ]
        self._table = pa.Table.from_arrays(columns, schema=SCHEMA)

        emb_matrix = np.stack(embedding_rows).astype(np.float32, copy=False)
        norms = np.linalg.norm(emb_matrix, axis=1, keepdims=True)
        # Rows without an embedding stay zero after normalisation (norm 0 → safe denom 1).
        safe_norms = np.where(norms == 0.0, 1.0, norms)
        self._embedding_norm = (emb_matrix / safe_norms).astype(np.float32, copy=False)
        self._has_embedding = np.asarray(has_embedding, dtype=bool)
        self._sig_verified = np.asarray(sig_verified, dtype=bool)
        self._integrity_failures = new_integrity_failures
        self._shard_id_to_row = {sid: i for i, sid in enumerate(shard_ids)}

    def _set_empty(self) -> None:
        self._table = SCHEMA.empty_table() if SCHEMA is not None else None
        self._embedding_norm = np.zeros((0, EMBEDDING_DIM), dtype=np.float32) if np is not None else None
        self._has_embedding = np.zeros((0,), dtype=bool) if np is not None else None
        self._sig_verified = np.zeros((0,), dtype=bool) if np is not None else None
        self._integrity_failures = {}
        self._shard_id_to_row = {}


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────────────────────

_cache: "ArrowShardCache | None" = None
_cache_lock = threading.Lock()


def get_arrow_cache() -> ArrowShardCache:
    """Return the process-wide ArrowShardCache singleton."""
    global _cache
    if _cache is not None:
        return _cache
    with _cache_lock:
        if _cache is None:
            from config import SHARD_DIR, INDEX_FILE
            _cache = ArrowShardCache(SHARD_DIR, INDEX_FILE)
        return _cache
