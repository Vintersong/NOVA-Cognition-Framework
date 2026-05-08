from __future__ import annotations

import subprocess
from types import SimpleNamespace
from typing import Any

import pytest

import evolve


def _make_fake_run(status_stdout: str, calls: list[list[str]]):
    def _fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append(cmd)
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return SimpleNamespace(returncode=0, stdout=status_stdout, stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    return _fake_run


def test_auto_commit_skips_non_allowlisted_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    # Mix: shards/ (denied), mcp/ (allowed), nova_usage.jsonl (denied)
    status = " M shards/foo.json\n M mcp/evolve.py\n M nova_usage.jsonl\n"

    monkeypatch.setattr(
        evolve,
        "subprocess",
        SimpleNamespace(run=_make_fake_run(status, calls), TimeoutExpired=subprocess.TimeoutExpired),
    )
    monkeypatch.setattr(evolve, "_run_tests", lambda: evolve.TestResult(ran=False))
    monkeypatch.setattr(evolve, "_build_commit_message", lambda files: "test")

    result = evolve._auto_commit(dry_run=False)

    git_add = next((cmd for cmd in calls if cmd[:2] == ["git", "add"]), None)
    assert git_add is not None
    assert "mcp/evolve.py" in git_add
    assert "shards/foo.json" not in git_add
    assert "nova_usage.jsonl" not in git_add
    assert result.committed is True


def test_auto_commit_returns_no_changes_when_only_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    status = " M shards/foo.json\n M nova_usage.jsonl\n"

    monkeypatch.setattr(
        evolve,
        "subprocess",
        SimpleNamespace(run=_make_fake_run(status, calls), TimeoutExpired=subprocess.TimeoutExpired),
    )
    monkeypatch.setattr(evolve, "_run_tests", lambda: evolve.TestResult(ran=False))

    result = evolve._auto_commit(dry_run=False)
    assert result.committed is False
    assert "allowlist" in result.reason


def test_auto_commit_uses_stash_on_test_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    status = " M mcp/evolve.py\n"

    monkeypatch.setattr(
        evolve,
        "subprocess",
        SimpleNamespace(run=_make_fake_run(status, calls), TimeoutExpired=subprocess.TimeoutExpired),
    )
    monkeypatch.setattr(evolve, "_run_tests", lambda: evolve.TestResult(ran=True, failed=2))

    result = evolve._auto_commit(dry_run=False)
    assert result.committed is False
    # Verify stash was invoked, not git checkout --
    stash_calls = [cmd for cmd in calls if cmd[:3] == ["git", "stash", "push"]]
    checkout_calls = [cmd for cmd in calls if cmd[:2] == ["git", "checkout"]]
    assert stash_calls, "expected `git stash push` for safe rollback"
    assert not checkout_calls, "must not use `git checkout --` (destructive)"
    assert "mcp/evolve.py" in stash_calls[0]
    assert "stashed" in result.reason


def test_env_override_replaces_default_commit_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    status = " M mcp/evolve.py\n M sandbox/play.py\n"

    monkeypatch.setenv("EVOLVE_COMMIT_ROOTS", "sandbox")
    monkeypatch.setattr(
        evolve,
        "subprocess",
        SimpleNamespace(run=_make_fake_run(status, calls), TimeoutExpired=subprocess.TimeoutExpired),
    )
    monkeypatch.setattr(evolve, "_run_tests", lambda: evolve.TestResult(ran=False))
    monkeypatch.setattr(evolve, "_build_commit_message", lambda files: "test")

    result = evolve._auto_commit(dry_run=False)
    git_add = next((cmd for cmd in calls if cmd[:2] == ["git", "add"]), None)
    assert git_add is not None
    assert "sandbox/play.py" in git_add
    assert "mcp/evolve.py" not in git_add  # mcp/ no longer allowed
    assert result.committed is True
