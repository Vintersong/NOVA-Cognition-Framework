from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

import atomic_io


def test_atomic_write_json_writes_payload(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    atomic_io.atomic_write_json(target, {"hello": "world", "n": 42})
    assert json.loads(target.read_text(encoding="utf-8")) == {"hello": "world", "n": 42}


def test_atomic_write_json_overwrites_existing(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    target.write_text('{"old": true}', encoding="utf-8")
    atomic_io.atomic_write_json(target, {"new": True})
    assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}


def test_atomic_write_json_preserves_prior_file_on_crash(tmp_path: Path) -> None:
    """If the writer crashes between truncate and replace, the existing file
    must remain intact — that's the entire reason this helper exists."""
    target = tmp_path / "data.json"
    target.write_text('{"prior": "state"}', encoding="utf-8")

    # Simulate crash: real os.replace becomes a raise. The tmp file should be
    # cleaned up; the destination should be untouched.
    with patch.object(atomic_io.os, "replace", side_effect=OSError("simulated crash")):
        with pytest.raises(OSError, match="simulated crash"):
            atomic_io.atomic_write_json(target, {"new": "value"})

    assert json.loads(target.read_text(encoding="utf-8")) == {"prior": "state"}
    # No leftover .tmp_*.json should remain.
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".tmp_")]
    assert leftovers == []


def test_atomic_write_text_writes_and_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "notes.md"
    atomic_io.atomic_write_text(target, "first")
    atomic_io.atomic_write_text(target, "second")
    assert target.read_text(encoding="utf-8") == "second"


def test_atomic_write_json_indent_none(tmp_path: Path) -> None:
    """indent=None produces compact output (used by the wiki index for size)."""
    target = tmp_path / "compact.json"
    atomic_io.atomic_write_json(target, {"a": [1, 2, 3]}, indent=None)
    assert "\n" not in target.read_text(encoding="utf-8").rstrip("\n")
