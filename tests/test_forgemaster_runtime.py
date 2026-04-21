from __future__ import annotations

from pathlib import Path

import pytest

import forgemaster_runtime as runtime
from permissions import ToolPermissionContext
from session_store import SessionStore


def _build_runtime(tmp_path: Path) -> runtime.ForgemasterRuntime:
    store = SessionStore(str(tmp_path / "sessions"))
    permissions = ToolPermissionContext.from_iterables(deny_tools=[], deny_prefixes=[])
    return runtime.ForgemasterRuntime(store, permissions)


def test_write_implementation_file_writes_inside_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "_REPO_ROOT", tmp_path)
    written = runtime._write_implementation_file("nested/output.py", "print('ok')\n")
    assert Path(written).exists()
    assert Path(written).read_text(encoding="utf-8") == "print('ok')\n"


def test_write_implementation_file_rejects_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "_REPO_ROOT", tmp_path)
    with pytest.raises(ValueError, match="outside repo root"):
        runtime._write_implementation_file("../escape.py", "x = 1\n")


def test_get_permitted_lanes_marks_implementer_restricted(tmp_path: Path) -> None:
    rt = _build_runtime(tmp_path)
    denied = ToolPermissionContext.from_iterables(
        deny_tools=list(runtime._WRITE_TOOLS), deny_prefixes=[]
    )
    lanes = rt.get_permitted_lanes(denied)
    assert "implementer:restricted" in lanes


def test_run_turn_dispatch_failure_surfaces_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rt = _build_runtime(tmp_path)
    session = rt.bootstrap("s1", [])
    monkeypatch.setattr(runtime, "_dispatch", lambda role, prompt: (_ for _ in ()).throw(RuntimeError("boom")))

    updated, response = rt.run_turn(
        session=session,
        role="planner",
        skill_path="missing-skill.md",
        prompt="hello",
    )
    assert "[DISPATCH FAILED: boom]" in response
    assert updated.messages[-1]["content"] == response
