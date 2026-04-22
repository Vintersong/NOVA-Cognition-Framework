from __future__ import annotations

"""
session_store.py — NovaSession and SessionStore for Phase 2.

NovaSession is a frozen dataclass — all updates return new instances
(same immutable pattern as UsageSummary in mcp/models.py).

SessionStore manages the lifecycle of sessions:
  - in-memory map for active sessions
  - flush to JSON on disk (one file per session)
  - load back from disk into memory
"""

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from filelock import FileLock

from models import UsageSummary

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _validate_session_id(session_id: str) -> str:
    """Validate user-controlled session IDs before filesystem use."""
    if not _SESSION_ID_RE.fullmatch(session_id):
        raise ValueError(
            "Invalid session_id. Use 1-128 chars: letters, numbers, '.', '_' or '-'."
        )
    return session_id


# ═══════════════════════════════════════════════════════════
# NovaSession
# ═══════════════════════════════════════════════════════════

@dataclass(frozen=True)
class NovaSession:
    """
    Immutable representation of a single Forgemaster sprint session.

    Every method that modifies state returns a *new* ``NovaSession``
    instance — the caller is responsible for replacing the old
    reference (e.g. via ``SessionStore.update``).
    """

    session_id: str
    messages: tuple[dict, ...]
    usage: UsageSummary
    created_at: str
    last_active: str

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def new(cls, session_id: str) -> NovaSession:
        """Create a fresh session with no messages and zero usage."""
        now = _now_iso()
        return cls(
            session_id=session_id,
            messages=(),
            usage=UsageSummary(),
            created_at=now,
            last_active=now,
        )

    @classmethod
    def from_dict(cls, data: dict) -> NovaSession:
        """Deserialise a session that was previously written by ``to_dict``."""
        usage_raw = data.get("usage", {})
        return cls(
            session_id=data["session_id"],
            messages=tuple(data.get("messages", [])),
            usage=UsageSummary(
                input_tokens=usage_raw.get("input_tokens", 0),
                output_tokens=usage_raw.get("output_tokens", 0),
            ),
            created_at=data.get("created_at", _now_iso()),
            last_active=data.get("last_active", _now_iso()),
        )

    # ------------------------------------------------------------------
    # Immutable update helpers
    # ------------------------------------------------------------------

    def add_message(self, role: str, content: str) -> NovaSession:
        """Return a new session with the message appended and ``last_active`` refreshed."""
        entry: dict = {
            "role": role,
            "content": content,
            "timestamp": _now_iso(),
        }
        new_messages = self.messages + (entry,)
        new_usage = self.usage.add_turn(
            prompt=content if role == "user" else "",
            output=content if role != "user" else "",
        )
        return NovaSession(
            session_id=self.session_id,
            messages=new_messages,
            usage=new_usage,
            created_at=self.created_at,
            last_active=_now_iso(),
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a plain-dict form suitable for JSON serialisation."""
        return {
            "session_id": self.session_id,
            "messages": list(self.messages),
            "usage": {
                "input_tokens": self.usage.input_tokens,
                "output_tokens": self.usage.output_tokens,
                "total_tokens": self.usage.total_tokens,
            },
            "created_at": self.created_at,
            "last_active": self.last_active,
        }


# ═══════════════════════════════════════════════════════════
# SessionStore
# ═══════════════════════════════════════════════════════════

class SessionStore:
    """
    Manages active and persisted ``NovaSession`` objects.

    Active sessions live in ``_sessions`` (in-memory dict).
    Persisted sessions are stored as ``{store_dir}/{session_id}.json``.
    ``flush`` moves a session from memory to disk.
    ``load`` moves a session from disk back into memory.
    """

    def __init__(self, store_dir: str) -> None:
        self._store_dir = Path(store_dir)
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, NovaSession] = {}

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(self, session_id: str) -> NovaSession:
        """Create and register a new in-memory session."""
        session_id = _validate_session_id(session_id)
        session = NovaSession.new(session_id)
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> Optional[NovaSession]:
        """Return the in-memory session or ``None`` if not active."""
        session_id = _validate_session_id(session_id)
        return self._sessions.get(session_id)

    def update(self, session: NovaSession) -> None:
        """Replace the in-memory session with the supplied instance."""
        _validate_session_id(session.session_id)
        self._sessions[session.session_id] = session

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def flush(self, session_id: str) -> None:
        """Write the session to disk and remove it from memory."""
        session_id = _validate_session_id(session_id)
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"Session '{session_id}' is not active in memory.")
        filepath = self._store_dir / f"{session_id}.json"
        lock_path = str(filepath) + ".lock"
        with FileLock(lock_path, timeout=5):
            with open(filepath, "w", encoding="utf-8") as fh:
                json.dump(session.to_dict(), fh, indent=2)
        del self._sessions[session_id]

    def load(self, session_id: str) -> NovaSession:
        """Read a session from disk into memory and return it."""
        session_id = _validate_session_id(session_id)
        filepath = self._store_dir / f"{session_id}.json"
        if not filepath.exists():
            raise FileNotFoundError(f"No persisted session found for '{session_id}'.")
        lock_path = str(filepath) + ".lock"
        with FileLock(lock_path, timeout=5):
            with open(filepath, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        session = NovaSession.from_dict(data)
        self._sessions[session_id] = session
        return session

    def list_sessions(self) -> list[str]:
        """Return session IDs of all JSON files currently persisted on disk."""
        return sorted(
            p.stem for p in self._store_dir.glob("*.json")
        )
