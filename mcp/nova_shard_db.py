"""
nova_shard_db.py — SQLite secondary index for NOVA JSON shards.

JSON files in shards/ are the source of truth. This DB is a fast-lookup
layer that enables single-predicate range queries across the full epistemic
state space (confidence × valence × arousal × epistemic), replacing
multi-column joins and full index scans.

Encoding (Balatro-inspired, integer substrate):
    state = CCCC_V_A_E
          = confidence(0-9999) × 1000 + valence(0-9) × 100 + arousal(0-9) × 10 + epistemic(0-2)

Defaults at creation (NÓTT updates later):
    valence=5, arousal=5 (neutral), epistemic derived from confidence float
    provenance="en|WEIRD|LLM"

Usage:
    db = NovaShardDB()          # opens mcp/nova_shard_index.db
    db.upsert_from_shard(data)  # called by store.save_shard
    db.rebuild_from_dir()       # called by utilities/build_nova_shard_db.py
    db.close()
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

from config import SHARD_DIR

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).parent
NOVA_SHARD_DB_PATH = os.environ.get(
    "NOVA_SHARD_DB_FILE",
    str(_REPO_ROOT / "nova_shard_index.db"),
)

# ═══════════════════════════════════════════════════════════
# ENCODING
# ═══════════════════════════════════════════════════════════

def encode_state(
    confidence: float,
    valence: int = 5,
    arousal: int = 5,
    epistemic: int = 1,
) -> int:
    """Pack four dimensions into a single integer.

    confidence  float [0.0, 1.0]  → 0–9999 (4-digit, most significant)
    valence     int   [0, 9]      — affective tone (0=negative, 9=positive)
    arousal     int   [0, 9]      — activation level (0=dormant, 9=high)
    epistemic   int   {0, 1, 2}   — 0=contradicted, 1=neutral, 2=confirmed

    Sort order: high confidence sorts first, epistemic is least significant.
    """
    c = max(0, min(9999, round(confidence * 10000)))
    v = max(0, min(9, int(valence)))
    a = max(0, min(9, int(arousal)))
    e = max(0, min(2, int(epistemic)))
    return c * 1000 + v * 100 + a * 10 + e


def decode_state(state: int) -> dict:
    """Unpack a structured state integer back into its four dimensions."""
    epistemic = state % 10
    arousal   = (state // 10) % 10
    valence   = (state // 100) % 10
    confidence = (state // 1000) / 10000.0
    return {
        "confidence": round(confidence, 4),
        "valence": valence,
        "arousal": arousal,
        "epistemic": epistemic,  # 0=contradicted, 1=neutral, 2=confirmed
    }


def _epistemic_from_confidence(confidence: float) -> int:
    """Derive ternary epistemic state from continuous confidence score."""
    if confidence >= 0.85:
        return 2  # confirmed
    if confidence < 0.40:
        return 0  # contradicted
    return 1      # neutral


# ═══════════════════════════════════════════════════════════
# DB
# ═══════════════════════════════════════════════════════════

class NovaShardDB:
    """SQLite secondary index for NOVA JSON shards.

    Stays in sync with JSON files via:
      - upsert_from_shard() called from store.save_shard on every write
      - rebuild_from_dir() for initial population or repair
    """

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or NOVA_SHARD_DB_PATH
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS shards (
                id               TEXT PRIMARY KEY,
                state            INTEGER NOT NULL,
                guiding_question TEXT NOT NULL DEFAULT '',
                confidence       REAL  NOT NULL DEFAULT 1.0,
                valence          INTEGER NOT NULL DEFAULT 5,
                arousal          INTEGER NOT NULL DEFAULT 5,
                epistemic        INTEGER NOT NULL DEFAULT 1,
                theme            TEXT NOT NULL DEFAULT '',
                intent           TEXT NOT NULL DEFAULT '',
                tags             TEXT NOT NULL DEFAULT '[]',
                provenance       TEXT NOT NULL DEFAULT 'en|WEIRD|LLM',
                last_used        TEXT NOT NULL DEFAULT '',
                usage_count      INTEGER NOT NULL DEFAULT 0,
                quarantine_until TEXT,
                mtime_ns         INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_state      ON shards (state);
            CREATE INDEX IF NOT EXISTS idx_confidence ON shards (confidence);
            CREATE INDEX IF NOT EXISTS idx_last_used  ON shards (last_used);
        """)
        self.conn.commit()

    # ── Write ─────────────────────────────────────────────────────────────

    def upsert_from_shard(self, shard_data: dict, mtime_ns: int = 0) -> bool:
        """Sync one shard JSON dict into the index. Called on every save_shard."""
        try:
            shard_id = shard_data.get("shard_id", "")
            if not shard_id:
                return False

            meta = shard_data.get("meta_tags", {})
            confidence = float(meta.get("confidence", 1.0))
            valence    = int(meta.get("valence", 5))
            arousal    = int(meta.get("arousal", 5))
            epistemic  = int(meta.get("epistemic", _epistemic_from_confidence(confidence)))
            provenance = str(meta.get("provenance", "en|WEIRD|LLM"))
            state      = encode_state(confidence, valence, arousal, epistemic)

            tags = json.dumps(shard_data.get("tags", []))

            self.conn.execute(
                """
                INSERT INTO shards
                    (id, state, guiding_question, confidence, valence, arousal,
                     epistemic, theme, intent, tags, provenance, last_used,
                     usage_count, quarantine_until, mtime_ns)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    state            = excluded.state,
                    guiding_question = excluded.guiding_question,
                    confidence       = excluded.confidence,
                    valence          = excluded.valence,
                    arousal          = excluded.arousal,
                    epistemic        = excluded.epistemic,
                    theme            = excluded.theme,
                    intent           = excluded.intent,
                    tags             = excluded.tags,
                    provenance       = excluded.provenance,
                    last_used        = excluded.last_used,
                    usage_count      = excluded.usage_count,
                    quarantine_until = excluded.quarantine_until,
                    mtime_ns         = excluded.mtime_ns
                """,
                (
                    shard_id,
                    state,
                    shard_data.get("guiding_question", ""),
                    confidence,
                    valence,
                    arousal,
                    epistemic,
                    meta.get("theme", ""),
                    meta.get("intent", ""),
                    tags,
                    provenance,
                    meta.get("last_used", ""),
                    int(meta.get("usage_count", 0)),
                    meta.get("quarantine_until"),
                    mtime_ns,
                ),
            )
            self.conn.commit()
            return True
        except (sqlite3.Error, ValueError, TypeError) as exc:
            logger.warning("nova_shard_db.upsert failed for %s: %s", shard_data.get("shard_id"), exc)
            return False

    def remove(self, shard_id: str) -> bool:
        try:
            self.conn.execute("DELETE FROM shards WHERE id = ?", (shard_id,))
            self.conn.commit()
            return True
        except sqlite3.Error:
            return False

    def remove_orphans(self, current_ids: set[str]) -> int:
        """Delete rows whose shard JSON no longer exists. Returns count removed."""
        try:
            rows = self.conn.execute("SELECT id FROM shards").fetchall()
            to_remove = [r["id"] for r in rows if r["id"] not in current_ids]
            if to_remove:
                self.conn.executemany(
                    "DELETE FROM shards WHERE id = ?",
                    [(sid,) for sid in to_remove],
                )
                self.conn.commit()
            return len(to_remove)
        except sqlite3.Error:
            return 0

    # ── Rebuild ───────────────────────────────────────────────────────────

    def rebuild_from_dir(self, shard_dir: str | None = None) -> dict:
        """Wipe and repopulate from all JSON files in shard_dir.

        Returns {"indexed": N, "failed": M}.
        """
        import json as _json

        root = Path(shard_dir or SHARD_DIR)
        if not root.is_dir():
            return {"indexed": 0, "failed": 0, "error": f"Directory not found: {root}"}

        try:
            self.conn.execute("DELETE FROM shards")
            self.conn.commit()
        except sqlite3.Error as exc:
            return {"indexed": 0, "failed": 0, "error": str(exc)}

        indexed = failed = 0
        for path in sorted(root.glob("*.json")):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = _json.load(f)
                mtime_ns = path.stat().st_mtime_ns
                if self.upsert_from_shard(data, mtime_ns):
                    indexed += 1
                else:
                    failed += 1
            except Exception as exc:
                logger.warning("nova_shard_db.rebuild skipping %s: %s", path.name, exc)
                failed += 1

        return {"indexed": indexed, "failed": failed}

    # ── Query ─────────────────────────────────────────────────────────────

    def query_state_range(
        self,
        min_confidence: float = 0.0,
        max_confidence: float = 1.0,
        epistemic: int | None = None,
        valence_min: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Range query on the structured state integer — the key advantage of this layer.

        Equivalent SQL (no multi-column joins needed):
            SELECT * FROM shards
            WHERE state BETWEEN encode(min_conf) AND encode(max_conf)
            AND state % 10 = epistemic   -- optional
        """
        min_state = encode_state(min_confidence, valence=0, arousal=0, epistemic=0)
        max_state = encode_state(max_confidence, valence=9, arousal=9, epistemic=2)

        sql = "SELECT * FROM shards WHERE state BETWEEN ? AND ?"
        params: list[Any] = [min_state, max_state]

        if epistemic is not None:
            sql += " AND state % 10 = ?"
            params.append(int(epistemic))

        if valence_min is not None:
            sql += " AND valence >= ?"
            params.append(int(valence_min))

        sql += " ORDER BY state DESC LIMIT ?"
        params.append(limit)

        try:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]
        except sqlite3.Error as exc:
            logger.warning("nova_shard_db.query_state_range failed: %s", exc)
            return []

    def search(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Keyword pre-filter across guiding_question + theme + intent.

        Faster than scanning shard_index.json for large corpora; used as a
        pre-filter before HUGINN LLM scoring.
        """
        if not query or not query.strip():
            return []

        tokens = [t for t in query.lower().split() if t]
        sql = "SELECT * FROM shards WHERE 1=1"
        params: list[Any] = []

        for tok in tokens:
            sql += " AND (LOWER(guiding_question) LIKE ? OR LOWER(theme) LIKE ? OR LOWER(intent) LIKE ?)"
            params.extend([f"%{tok}%", f"%{tok}%", f"%{tok}%"])

        sql += " ORDER BY state DESC LIMIT ?"
        params.append(limit)

        try:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]
        except sqlite3.Error as exc:
            logger.warning("nova_shard_db.search failed: %s", exc)
            return []

    def ids_with_prefix(self, prefix: str, *, limit: int = 50) -> list[str]:
        """Shard ids starting with *prefix*, for argument completion.

        ``id`` is the table's primary key, so ``LIKE 'pfx%'`` is an index scan
        rather than a table scan. :meth:`search` deliberately excludes ``id``
        from its match columns, so this is a separate accessor rather than a
        flag on that one. Highest state (confidence-dominant) first, so a
        truncated list is the useful half.
        """
        # LIKE's own wildcards have to be escaped or a slug containing % or _
        # would match far more than the user typed.
        escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        try:
            rows = self.conn.execute(
                "SELECT id FROM shards WHERE id LIKE ? ESCAPE '\\' "
                "ORDER BY state DESC, id ASC LIMIT ?",
                (escaped + "%", limit),
            ).fetchall()
        except sqlite3.Error as exc:
            logger.warning("nova_shard_db.ids_with_prefix failed: %s", exc)
            return []
        return [r["id"] for r in rows]

    def stats(self) -> dict:
        """Row count and state distribution summary."""
        try:
            total = self.conn.execute("SELECT COUNT(*) FROM shards").fetchone()[0]
            confirmed = self.conn.execute(
                "SELECT COUNT(*) FROM shards WHERE state % 10 = 2"
            ).fetchone()[0]
            contradicted = self.conn.execute(
                "SELECT COUNT(*) FROM shards WHERE state % 10 = 0"
            ).fetchone()[0]
            quarantined = self.conn.execute(
                "SELECT COUNT(*) FROM shards WHERE quarantine_until IS NOT NULL"
            ).fetchone()[0]
            return {
                "total": total,
                "confirmed": confirmed,
                "neutral": total - confirmed - contradicted,
                "contradicted": contradicted,
                "quarantined": quarantined,
            }
        except sqlite3.Error:
            return {}

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "NovaShardDB":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


# ── Module-level singleton (lazy, so import never blocks) ─────────────────

_db_singleton: NovaShardDB | None = None


def get_nova_shard_db() -> NovaShardDB:
    global _db_singleton
    if _db_singleton is None:
        _db_singleton = NovaShardDB()
    return _db_singleton
