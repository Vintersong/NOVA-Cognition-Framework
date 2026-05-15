"""
audit_log.py — SQLite-backed HITL audit log for NOVA.

Tracks the four-state HITL lifecycle for irreversible capability calls:
  irreversible.request  — agent emitted tool-call envelope
  irreversible.decision — broker returned approve/deny
  irreversible.executed — runtime performed the call (ok=True) or failed (ok=False)
  capability.denied     — call blocked because capability not declared

All records for one HITL cycle share a request_id so the three phases
(request → decision → executed) can be linked in queries.

Also provides run_biconditional_check() per Metere (2026) §4:

  D = actual corpus delta (shard IDs added/changed in session)
  S = { target for records where type=irreversible.executed AND ok=1 }

  Check passes iff D == S.  Either direction broken is a failure:
    F1 unaccounted_changes: side-effect without audit record (gate bypass)
    F2 phantom_records:     audit record whose corpus change is absent
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from tool_registry import get as _get_spec, irreversible_tools

logger = logging.getLogger(__name__)


# Derived from `mcp/tool_registry.py:_REGISTRY` — shard-category irreversibles
# whose audit targets are shard IDs (vs file paths from Forgemaster writes).
_SHARD_TOOL_NAMES: frozenset[str] = frozenset(
    name for name in irreversible_tools() if _get_spec(name).category == "shard"
)


class AuditLog:
    """Thread-safe (WAL mode) SQLite audit log for HITL lifecycle events."""

    def __init__(self, db_path: str) -> None:
        self._path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id           TEXT PRIMARY KEY,
                session_id   TEXT NOT NULL,
                request_id   TEXT NOT NULL,
                type         TEXT NOT NULL,
                tool_name    TEXT NOT NULL,
                target       TEXT,
                skill_id     TEXT NOT NULL,
                verification TEXT NOT NULL,
                ok           INTEGER,
                ts           TEXT NOT NULL
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_al_session ON audit_log (session_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_al_request ON audit_log (request_id)"
        )
        self._conn.commit()

    _ALLOWED_COLS = frozenset(
        {"id", "session_id", "request_id", "type", "tool_name",
         "target", "skill_id", "verification", "ok", "ts"}
    )

    def _insert(self, **row: object) -> None:
        row.setdefault("id", str(uuid.uuid4()))
        row.setdefault("ts", datetime.now(timezone.utc).isoformat())
        filtered = {k: v for k, v in row.items() if k in self._ALLOWED_COLS}
        cols = ", ".join(filtered.keys())
        placeholders = ", ".join("?" * len(filtered))
        with self._lock:
            self._conn.execute(
                f"INSERT INTO audit_log ({cols}) VALUES ({placeholders})",
                list(filtered.values()),
            )
            self._conn.commit()

    # ── HITL lifecycle writers ────────────────────────────────────────────────

    def log_request(
        self,
        session_id: str,
        request_id: str,
        tool_name: str,
        skill_id: str,
        verification: str,
        target: Optional[str] = None,
    ) -> None:
        self._insert(
            session_id=session_id,
            request_id=request_id,
            type="irreversible.request",
            tool_name=tool_name,
            target=target,
            skill_id=skill_id,
            verification=verification,
            ok=None,
        )

    def log_decision(
        self,
        session_id: str,
        request_id: str,
        tool_name: str,
        skill_id: str,
        verification: str,
        approved: bool,
    ) -> None:
        self._insert(
            session_id=session_id,
            request_id=request_id,
            type="irreversible.decision",
            tool_name=tool_name,
            target=None,
            skill_id=skill_id,
            verification=verification,
            ok=1 if approved else 0,
        )

    def log_executed(
        self,
        session_id: str,
        request_id: str,
        tool_name: str,
        skill_id: str,
        verification: str,
        target: Optional[str] = None,
        ok: bool = True,
    ) -> None:
        self._insert(
            session_id=session_id,
            request_id=request_id,
            type="irreversible.executed",
            tool_name=tool_name,
            target=target,
            skill_id=skill_id,
            verification=verification,
            ok=1 if ok else 0,
        )

    def log_denied(
        self,
        session_id: str,
        request_id: str,
        tool_name: str,
        skill_id: str,
        verification: str,
        reason: str,
    ) -> None:
        self._insert(
            session_id=session_id,
            request_id=request_id,
            type="capability.denied",
            tool_name=tool_name,
            target=reason,
            skill_id=skill_id,
            verification=verification,
            ok=0,
        )

    # ── Biconditional post-run audit check ───────────────────────────────────

    def get_executed_records(self, session_id: str) -> list[dict]:
        """Return all ok=1 irreversible.executed records for *session_id*."""
        cur = self._conn.execute(
            """
            SELECT tool_name, target, request_id
            FROM audit_log
            WHERE session_id = ?
              AND type = 'irreversible.executed'
              AND ok = 1
            """,
            (session_id,),
        )
        return [
            {"tool_name": row[0], "target": row[1], "request_id": row[2]}
            for row in cur.fetchall()
        ]

    # Tools whose targets are shard IDs (vs file paths from forgemaster writes).
    # Derived from `mcp/tool_registry.py` — keep this class attribute as the
    # in-class alias so existing self._SHARD_TOOLS usages keep working.
    _SHARD_TOOLS = _SHARD_TOOL_NAMES

    def run_biconditional_check(
        self,
        session_id: str,
        corpus_before: "dict[str, float]",
        corpus_after: "dict[str, float]",
        files_written: "set[str] | None" = None,
    ) -> dict:
        """
        Verify corpus delta matches executed audit records, partitioned by target type.

        Shard check  — D = stems whose mtime changed, were added, or were deleted
                        S_shards = targets from shard-tool records
                        F1 unaccounted_changes: D - S_shards (gate bypass)
                        F2 phantom_records:     S_shards - D (spurious record)

        File check   — only run when *files_written* is supplied by the caller.
                        S_files = targets from non-shard-tool records (fs.write.irrev)
                        Compares against the actual files written this sprint.

        Keeping these categories separate avoids false positives from the
        prior design where shard IDs and file paths were compared in one set.

        corpus_before / corpus_after are stem → mtime dicts so that
        modifications and deletions are detected, not only additions.
        """
        all_stems = set(corpus_before) | set(corpus_after)
        D = {
            stem for stem in all_stems
            if corpus_before.get(stem) != corpus_after.get(stem)
        }
        records = self.get_executed_records(session_id)

        shard_S = {
            r["target"] for r in records
            if r["target"] and r["tool_name"] in self._SHARD_TOOLS
        }
        file_S = {
            r["target"] for r in records
            if r["target"] and r["tool_name"] not in self._SHARD_TOOLS
        }

        unaccounted = sorted(D - shard_S)
        phantom = sorted(shard_S - D)

        file_unaccounted: list[str] = []
        file_phantom: list[str] = []
        if files_written is not None:
            file_unaccounted = sorted(files_written - file_S)
            file_phantom = sorted(file_S - files_written)

        passed = (
            not unaccounted and not phantom
            and not file_unaccounted and not file_phantom
        )

        result = {
            "session_id": session_id,
            "passed": passed,
            "corpus_delta": sorted(D),
            "shard_audit_records": sorted(shard_S),
            "file_audit_records": sorted(file_S),
            "unaccounted_changes": unaccounted,
            "phantom_records": phantom,
            "file_unaccounted": file_unaccounted,
            "file_phantom": file_phantom,
        }

        if not passed:
            logger.warning(
                "audit_log.biconditional_check FAILED session=%s "
                "unaccounted=%s phantom=%s file_unaccounted=%s file_phantom=%s",
                session_id, unaccounted, phantom, file_unaccounted, file_phantom,
            )
        else:
            logger.info(
                "audit_log.biconditional_check PASSED session=%s "
                "shard_delta=%d file_delta=%d",
                session_id, len(D), len(file_S),
            )

        return result
