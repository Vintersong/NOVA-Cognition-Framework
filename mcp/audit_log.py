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
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class AuditLog:
    """Thread-safe (WAL mode) SQLite audit log for HITL lifecycle events."""

    def __init__(self, db_path: str) -> None:
        self._path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
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

    def _insert(self, **row: object) -> None:
        row.setdefault("id", str(uuid.uuid4()))
        row.setdefault("ts", datetime.now(timezone.utc).isoformat())
        cols = ", ".join(str(k) for k in row)
        placeholders = ", ".join("?" * len(row))
        self._conn.execute(
            f"INSERT INTO audit_log ({cols}) VALUES ({placeholders})",
            list(row.values()),
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

    def run_biconditional_check(
        self,
        session_id: str,
        corpus_before: set[str],
        corpus_after: set[str],
    ) -> dict:
        """
        Verify corpus delta matches the set of executed audit records.

        D = corpus_after - corpus_before
        S = { record.target for executed records with ok=1 }

        Returns a result dict with keys:
          passed, corpus_delta, audit_records,
          unaccounted_changes (D-S), phantom_records (S-D)
        """
        D = corpus_after - corpus_before
        records = self.get_executed_records(session_id)
        S = {r["target"] for r in records if r["target"]}

        unaccounted = sorted(D - S)
        phantom = sorted(S - D)
        passed = not unaccounted and not phantom

        result = {
            "session_id": session_id,
            "passed": passed,
            "corpus_delta": sorted(D),
            "audit_records": sorted(S),
            "unaccounted_changes": unaccounted,
            "phantom_records": phantom,
        }

        if not passed:
            logger.warning(
                "audit_log.biconditional_check FAILED session=%s "
                "unaccounted=%s phantom=%s",
                session_id,
                unaccounted,
                phantom,
            )
        else:
            logger.info(
                "audit_log.biconditional_check PASSED session=%s delta=%d",
                session_id,
                len(D),
            )

        return result
