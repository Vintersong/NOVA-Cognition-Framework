from __future__ import annotations

from pathlib import Path

import pytest

import forgemaster_runtime as runtime


def test_writes_under_output_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "_REPO_ROOT", tmp_path)
    written = runtime._write_implementation_file("output/sprint_42/impl.py", "x = 1\n")
    assert Path(written).is_relative_to(tmp_path / "output")


def test_writes_under_intake_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "_REPO_ROOT", tmp_path)
    written = runtime._write_implementation_file("intake/draft.md", "# notes\n")
    assert Path(written).is_relative_to(tmp_path / "intake")


def test_rejects_path_outside_allowlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "_REPO_ROOT", tmp_path)
    with pytest.raises(ValueError, match="outside allowlist"):
        runtime._write_implementation_file("mcp/nova_server.py", "malicious = True\n")


def test_rejects_dotdot_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "_REPO_ROOT", tmp_path)
    with pytest.raises(ValueError, match=r"'\.\.' traversal"):
        runtime._write_implementation_file("output/../mcp/evil.py", "pwned = 1\n")


def test_rejects_absolute_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "_REPO_ROOT", tmp_path)
    abs_path = str(tmp_path / "output" / "abs.py")
    with pytest.raises(ValueError, match="absolute path"):
        runtime._write_implementation_file(abs_path, "x = 1\n")


def test_env_override_extends_allowlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "_REPO_ROOT", tmp_path)
    monkeypatch.setenv("FORGEMASTER_WRITE_ROOTS", "sandbox,output")
    written = runtime._write_implementation_file("sandbox/play.py", "ok = True\n")
    assert Path(written).is_relative_to(tmp_path / "sandbox")


def test_env_override_replaces_default_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "_REPO_ROOT", tmp_path)
    monkeypatch.setenv("FORGEMASTER_WRITE_ROOTS", "sandbox")
    # output/ is no longer allowed — env override fully replaces defaults
    with pytest.raises(ValueError, match="outside allowlist"):
        runtime._write_implementation_file("output/now_blocked.py", "x = 1\n")
