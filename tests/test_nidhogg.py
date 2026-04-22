from __future__ import annotations

from pathlib import Path

import pytest

import nidhogg


def test_resolve_allowed_ingest_path_accepts_allowed_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    intake = tmp_path / "intake"
    intake.mkdir()
    source = intake / "doc.txt"
    source.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(nidhogg, "NIDHOGG_ALLOWED_ROOTS", (str(intake.resolve()),))

    resolved = nidhogg._resolve_allowed_ingest_path(str(source))
    assert resolved == str(source.resolve())


def test_resolve_allowed_ingest_path_rejects_outside_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    intake = tmp_path / "intake"
    intake.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("nope", encoding="utf-8")
    monkeypatch.setattr(nidhogg, "NIDHOGG_ALLOWED_ROOTS", (str(intake.resolve()),))

    with pytest.raises(ValueError, match="Access denied"):
        nidhogg._resolve_allowed_ingest_path(str(outside))
