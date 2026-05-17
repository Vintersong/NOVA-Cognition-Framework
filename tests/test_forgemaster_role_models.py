"""Tests for FORGEMASTER_*_MODEL per-role overrides.

The env vars are read at module-import time in ``mcp/config.py``. To exercise
the override behavior, we mutate ``os.environ`` directly inside the fixture
and reload both ``config`` and ``forgemaster_runtime`` so ``_ROLE_TO_MODEL``
rebinds against the new values.

The fixture deliberately does NOT depend on pytest's ``monkeypatch`` fixture:
pytest tears the test fixture's finalizer down BEFORE monkeypatch undoes its
env changes, so a monkeypatch-driven cleanup here would re-read the test's
overridden env back into the modules. Instead we snapshot/restore env
ourselves around the reload so teardown is correct regardless of order.
"""

from __future__ import annotations

import importlib
import os

import pytest


_VARS = (
    "FORGEMASTER_ORCHESTRATOR_MODEL",
    "FORGEMASTER_PLANNER_MODEL",
    "FORGEMASTER_REVIEWER_MODEL",
    "FORGEMASTER_IMPLEMENTER_MODEL",
)


@pytest.fixture
def reload_with_env():
    """Reload config + forgemaster_runtime under explicit env, restore at teardown."""
    import config
    import forgemaster_runtime as runtime

    snapshot = {var: os.environ.get(var) for var in _VARS}

    def _apply(env_overrides: dict[str, str] | None = None) -> object:
        for var in _VARS:
            os.environ.pop(var, None)
        for var, val in (env_overrides or {}).items():
            os.environ[var] = val
        importlib.reload(config)
        return importlib.reload(runtime)

    yield _apply

    for var, val in snapshot.items():
        if val is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = val
    importlib.reload(config)
    importlib.reload(runtime)


def test_role_models_default_to_muninn_and_gemini(reload_with_env) -> None:
    """With no FORGEMASTER_*_MODEL env vars set, roles inherit MUNINN/GEMINI."""
    runtime = reload_with_env()

    assert runtime._ROLE_TO_MODEL["orchestrator"] == runtime.MUNINN_MODEL
    assert runtime._ROLE_TO_MODEL["planner"] == runtime.MUNINN_MODEL
    assert runtime._ROLE_TO_MODEL["reviewer"] == runtime.MUNINN_MODEL
    assert runtime._ROLE_TO_MODEL["implementer"] == runtime.GEMINI_MODEL


def test_orchestrator_model_override_propagates(reload_with_env) -> None:
    """Setting FORGEMASTER_ORCHESTRATOR_MODEL swaps that role without touching the others."""
    runtime = reload_with_env({"FORGEMASTER_ORCHESTRATOR_MODEL": "claude-opus-test-alias"})

    assert runtime._ROLE_TO_MODEL["orchestrator"] == "claude-opus-test-alias"
    assert runtime._ROLE_TO_MODEL["planner"] == runtime.MUNINN_MODEL
    assert runtime._ROLE_TO_MODEL["reviewer"] == runtime.MUNINN_MODEL
    assert runtime._ROLE_TO_MODEL["implementer"] == runtime.GEMINI_MODEL


def test_all_four_overrides_propagate(reload_with_env) -> None:
    """All four overrides applied together each land on the correct role."""
    runtime = reload_with_env({
        "FORGEMASTER_ORCHESTRATOR_MODEL": "model-orc",
        "FORGEMASTER_PLANNER_MODEL": "model-plan",
        "FORGEMASTER_REVIEWER_MODEL": "model-rev",
        "FORGEMASTER_IMPLEMENTER_MODEL": "model-impl",
    })

    assert runtime._ROLE_TO_MODEL["orchestrator"] == "model-orc"
    assert runtime._ROLE_TO_MODEL["planner"] == "model-plan"
    assert runtime._ROLE_TO_MODEL["reviewer"] == "model-rev"
    assert runtime._ROLE_TO_MODEL["implementer"] == "model-impl"


def test_gemini_override_threads_through_to_call_gemini(monkeypatch: pytest.MonkeyPatch, reload_with_env) -> None:
    """A non-default Gemini model on the implementer role reaches _call_gemini.

    Reviewer (codex P2 / gemini-code-assist high) caught that _call_gemini was
    hardcoded to GEMINI_MODEL — overrides like FORGEMASTER_IMPLEMENTER_MODEL=
    gemini-2.5-pro would dispatch as Google but actually call the default
    Flash model. This test guards against regression by capturing the model
    arg passed into _call_gemini.
    """
    runtime = reload_with_env({"FORGEMASTER_IMPLEMENTER_MODEL": "gemini-test-override"})

    captured: dict[str, str] = {}

    def _fake_call_gemini(prompt: str, model: str = runtime.GEMINI_MODEL, max_tokens: int = 4096):
        captured["model"] = model
        return ("ok", 0, 0, 0)

    monkeypatch.setattr(runtime, "_call_gemini", _fake_call_gemini)

    runtime._dispatch("implementer", "hello")

    assert captured["model"] == "gemini-test-override"
