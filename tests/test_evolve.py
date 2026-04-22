from __future__ import annotations

from types import SimpleNamespace

import pytest

import evolve


def test_auto_commit_uses_double_dash_for_git_add(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):  # noqa: ANN001
        calls.append(cmd)
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return SimpleNamespace(returncode=0, stdout=" M --odd.py\n", stderr="")
        if cmd[:2] == ["git", "add"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:2] == ["git", "commit"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(evolve, "subprocess", SimpleNamespace(run=_fake_run, TimeoutExpired=TimeoutError))
    monkeypatch.setattr(evolve, "_run_tests", lambda: evolve.TestResult(ran=False))
    monkeypatch.setattr(evolve, "_build_commit_message", lambda files: "test commit")

    result = evolve._auto_commit(dry_run=False)
    assert result.committed is True
    git_add_calls = [cmd for cmd in calls if cmd[:2] == ["git", "add"]]
    assert git_add_calls
    assert git_add_calls[0][2] == "--"
