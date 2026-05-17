"""Tests for FORGEMASTER_*_MODEL per-role overrides.

The env vars are read at module-import time in ``mcp/config.py``. To exercise
the override behavior, we monkeypatch the env then reload both ``config`` and
``forgemaster_runtime`` so ``_ROLE_TO_MODEL`` rebinds against the new values.
The fixture restores the original modules at teardown so other tests are
unaffected.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def reloaded_runtime(monkeypatch: pytest.MonkeyPatch):
    """Reload config + forgemaster_runtime under the test's env, then restore."""
    import config
    import forgemaster_runtime as runtime

    original_config = config
    original_runtime = runtime

    def _reload():
        importlib.reload(config)
        return importlib.reload(runtime)

    yield _reload

    importlib.reload(original_config)
    importlib.reload(original_runtime)


def test_role_models_default_to_muninn_and_gemini(monkeypatch: pytest.MonkeyPatch, reloaded_runtime) -> None:
    """With no FORGEMASTER_*_MODEL env vars set, roles inherit MUNINN/GEMINI."""
    for var in (
        "FORGEMASTER_ORCHESTRATOR_MODEL",
        "FORGEMASTER_PLANNER_MODEL",
        "FORGEMASTER_REVIEWER_MODEL",
        "FORGEMASTER_IMPLEMENTER_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)

    runtime = reloaded_runtime()

    assert runtime._ROLE_TO_MODEL["orchestrator"] == runtime.MUNINN_MODEL
    assert runtime._ROLE_TO_MODEL["planner"] == runtime.MUNINN_MODEL
    assert runtime._ROLE_TO_MODEL["reviewer"] == runtime.MUNINN_MODEL
    assert runtime._ROLE_TO_MODEL["implementer"] == runtime.GEMINI_MODEL


def test_orchestrator_model_override_propagates(monkeypatch: pytest.MonkeyPatch, reloaded_runtime) -> None:
    """Setting FORGEMASTER_ORCHESTRATOR_MODEL swaps that role without touching the others."""
    monkeypatch.setenv("FORGEMASTER_ORCHESTRATOR_MODEL", "claude-opus-test-alias")
    for var in (
        "FORGEMASTER_PLANNER_MODEL",
        "FORGEMASTER_REVIEWER_MODEL",
        "FORGEMASTER_IMPLEMENTER_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)

    runtime = reloaded_runtime()

    assert runtime._ROLE_TO_MODEL["orchestrator"] == "claude-opus-test-alias"
    assert runtime._ROLE_TO_MODEL["planner"] == runtime.MUNINN_MODEL
    assert runtime._ROLE_TO_MODEL["reviewer"] == runtime.MUNINN_MODEL
    assert runtime._ROLE_TO_MODEL["implementer"] == runtime.GEMINI_MODEL


def test_all_four_overrides_propagate(monkeypatch: pytest.MonkeyPatch, reloaded_runtime) -> None:
    """All four overrides applied together each land on the correct role."""
    monkeypatch.setenv("FORGEMASTER_ORCHESTRATOR_MODEL", "model-orc")
    monkeypatch.setenv("FORGEMASTER_PLANNER_MODEL", "model-plan")
    monkeypatch.setenv("FORGEMASTER_REVIEWER_MODEL", "model-rev")
    monkeypatch.setenv("FORGEMASTER_IMPLEMENTER_MODEL", "model-impl")

    runtime = reloaded_runtime()

    assert runtime._ROLE_TO_MODEL["orchestrator"] == "model-orc"
    assert runtime._ROLE_TO_MODEL["planner"] == "model-plan"
    assert runtime._ROLE_TO_MODEL["reviewer"] == "model-rev"
    assert runtime._ROLE_TO_MODEL["implementer"] == "model-impl"
