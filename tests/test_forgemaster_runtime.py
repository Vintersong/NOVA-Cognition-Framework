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


def test_write_implementation_file_writes_inside_allowlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "_REPO_ROOT", tmp_path)
    written = runtime._write_implementation_file("output/nested/result.py", "print('ok')\n")
    assert Path(written).exists()
    assert Path(written).read_text(encoding="utf-8") == "print('ok')\n"


def test_write_implementation_file_rejects_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "_REPO_ROOT", tmp_path)
    with pytest.raises(ValueError, match=r"'\.\.' traversal"):
        runtime._write_implementation_file("output/../escape.py", "x = 1\n")


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
    monkeypatch.setattr(runtime, "_dispatch", lambda role, prompt, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    updated, response, _ = rt.run_turn(
        session=session,
        role="planner",
        skill_path="missing-skill.md",
        prompt="hello",
    )
    assert "[DISPATCH FAILED: boom]" in response
    assert updated.messages[-1]["content"] == response


def _fake_dispatch(calls: list, fail_role: str = ""):
    def _dispatch(role, prompt, cached_system="", model_override=""):
        model = model_override or runtime._ROLE_TO_MODEL[role]
        calls.append((role, model))
        if role == fail_role:
            raise RuntimeError("boom")
        text = "PASS\nlooks fine" if role == "reviewer" else "print('new')\n"
        return text, model, 1, 1, 1
    return _dispatch


def _sprint_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "_REPO_ROOT", tmp_path)
    monkeypatch.setenv("FORGEMASTER_EVENT_LOG", str(tmp_path / "events.jsonl"))
    monkeypatch.setattr(runtime, "_empirical_stats_loaded", False)


_DOC = "Target file: `output/app.py`\nDo the thing."


def test_implementer_dispatch_failure_aborts_without_writing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _sprint_env(tmp_path, monkeypatch)
    target = tmp_path / "output" / "app.py"
    target.parent.mkdir(parents=True)
    target.write_text("original\n", encoding="utf-8")
    calls: list = []
    monkeypatch.setattr(runtime, "_dispatch", _fake_dispatch(calls, fail_role="implementer"))

    rt = _build_runtime(tmp_path)
    with pytest.raises(runtime.SprintAborted, match="implementer"):
        rt.run_sprint("s-abort", _DOC)

    assert target.read_text(encoding="utf-8") == "original\n"
    assert [role for role, _ in calls] == ["orchestrator", "planner", "implementer"]


def test_overwrite_keeps_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "_REPO_ROOT", tmp_path)
    runtime._write_implementation_file("output/a.py", "v1\n")
    runtime._write_implementation_file("output/a.py", "v2\n")
    assert (tmp_path / "output" / "a.py").read_text(encoding="utf-8") == "v2\n"
    assert (tmp_path / "output" / "a.py.bak").read_text(encoding="utf-8") == "v1\n"


def test_untyped_ticket_uses_configured_implementer_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _sprint_env(tmp_path, monkeypatch)
    calls: list = []
    monkeypatch.setattr(runtime, "_dispatch", _fake_dispatch(calls))

    summary = _build_runtime(tmp_path).run_sprint("s-route", _DOC)

    impl_model = dict(calls)["implementer"]
    assert impl_model == runtime._ROLE_TO_MODEL["implementer"]
    assert summary["routed_model"] == impl_model


def test_review_pass_does_not_corroborate_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _sprint_env(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime, "_dispatch", _fake_dispatch([]))
    written: list = []
    monkeypatch.setattr(runtime, "add_corroborated_by", lambda a, b: written.append((a, b)))
    monkeypatch.setattr(runtime, "FORGEMASTER_CORROBORATE_ON_REVIEW", False)

    summary = _build_runtime(tmp_path).run_sprint("s-corr", _DOC, shard_ids=["shard_a"])

    assert summary["outcome"] == "pass"
    assert summary["corroborated_shards"] == []
    assert written == []


def test_empirical_stats_skip_verdicts_from_other_reviewers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    log = tmp_path / "events.jsonl"
    monkeypatch.setenv("FORGEMASTER_EVENT_LOG", str(log))
    same = dict(runtime._ROLE_TO_MODEL)
    other = {**same, "reviewer": "claude-other-reviewer"}
    events = [
        {"role": "outcome", "event": "sprint_verdict", "task_type": "Boilerplate",
         "routed_model": "gemini-x", "outcome": "review_pass", "role_models": same},
        {"role": "outcome", "event": "sprint_verdict", "task_type": "boilerplate",
         "routed_model": "gemini-x", "outcome": "review_fail", "role_models": other},
        {"role": "outcome", "event": "sprint_verdict", "task_type": "boilerplate",
         "routed_model": "gemini-x", "outcome": "review_fail"},
    ]
    log.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")

    stats = runtime._load_empirical_stats()
    assert stats == {("boilerplate", "gemini-x"): {"pass": 1, "fail": 0}}


def test_number_lines() -> None:
    assert runtime._number_lines("a\nb") == "1 | a\n2 | b"


def test_reviewer_sees_numbered_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _sprint_env(tmp_path, monkeypatch)
    prompts: dict = {}
    inner = _fake_dispatch([])

    def _dispatch(role, prompt, **kwargs):
        prompts[role] = prompt
        return inner(role, prompt, **kwargs)

    monkeypatch.setattr(runtime, "_dispatch", _dispatch)
    _build_runtime(tmp_path).run_sprint("s-lines", _DOC)
    assert "1 | print('new')" in prompts["reviewer"]


def test_call_anthropic_joins_text_blocks_and_flags_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types

    from types import SimpleNamespace as NS

    reply = {"stop_reason": "end_turn"}

    class _Client:
        def __init__(self, api_key):
            self.messages = self

        def create(self, **kwargs):
            return NS(
                content=[NS(type="thinking", thinking="..."), NS(type="text", text="a"), NS(type="text", text="b")],
                usage=NS(input_tokens=1, output_tokens=2),
                stop_reason=reply["stop_reason"],
            )

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=_Client))
    monkeypatch.setenv("CLAUDE_API_KEY", "k")
    monkeypatch.setattr(runtime, "_REPO_ROOT", Path("/nonexistent"))

    assert runtime._call_anthropic("claude-x", "hi")[0] == "ab"
    reply["stop_reason"] = "max_tokens"
    with pytest.raises(RuntimeError, match="truncated"):
        runtime._call_anthropic("claude-x", "hi")


def test_dispatch_gives_implementer_larger_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def _gemini(prompt, model, max_tokens):
        seen["max_tokens"] = max_tokens
        return "x", 0, 0, 0

    monkeypatch.setattr(runtime, "_call_gemini", _gemini)
    runtime._dispatch("implementer", "p", model_override="gemini-x")
    assert seen["max_tokens"] == runtime._ROLE_MAX_TOKENS["implementer"]


def test_route_ticket_explores_least_sampled_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    stats = {
        ("boilerplate", "gemini-best"): {"pass": 10, "fail": 0},
        ("boilerplate", "claude-tried"): {"pass": 3, "fail": 3},
    }
    monkeypatch.setattr(runtime, "_load_empirical_stats", lambda: stats)
    monkeypatch.setattr(runtime, "_empirical_stats_loaded", False)
    monkeypatch.setattr(runtime, "_ROUTING_TABLE", {"boilerplate": "claude-tried"})
    monkeypatch.setitem(runtime._ROLE_TO_MODEL, "implementer", "gemini-unseen")
    rt = runtime.ForgemasterRuntime(None, None)

    monkeypatch.setattr(runtime.random, "random", lambda: 0.99)
    assert rt.route_ticket("boilerplate")[0] == "gemini-best"
    monkeypatch.setattr(runtime.random, "random", lambda: 0.0)
    assert rt.route_ticket("boilerplate")[0] == "gemini-unseen"


def test_corroboration_registers_sprint_entity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _sprint_env(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime, "_dispatch", _fake_dispatch([]))
    monkeypatch.setattr(runtime, "FORGEMASTER_CORROBORATE_ON_REVIEW", True)
    order: list = []
    monkeypatch.setattr(runtime, "register_external_entity", lambda eid, data: order.append(("entity", eid, data["type"])))
    monkeypatch.setattr(runtime, "add_corroborated_by", lambda a, b: order.append(("edge", a, b)))

    summary = _build_runtime(tmp_path).run_sprint("s-ent", _DOC, shard_ids=["shard_a"])

    assert summary["corroborated_shards"] == ["shard_a"]
    assert order == [("entity", "s-ent", "ForgemasterSprint"), ("edge", "shard_a", "s-ent")]
