from __future__ import annotations

from pathlib import Path

import pytest

from session_store import SessionStore


def test_session_store_roundtrip_with_safe_id(tmp_path: Path) -> None:
    store = SessionStore(str(tmp_path / "sessions"))
    session = store.create("sprint-1.alpha")
    store.update(session.add_message("user", "hello"))
    store.flush("sprint-1.alpha")
    restored = store.load("sprint-1.alpha")
    assert restored.session_id == "sprint-1.alpha"
    assert len(restored.messages) == 1


def test_session_store_rejects_invalid_session_id(tmp_path: Path) -> None:
    store = SessionStore(str(tmp_path / "sessions"))
    with pytest.raises(ValueError, match="Invalid session_id"):
        store.create("../escape")
