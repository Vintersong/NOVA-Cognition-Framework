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


_ROLES = ("ORCHESTRATOR", "PLANNER", "REVIEWER", "IMPLEMENTER")
_VARS = tuple(
    f"FORGEMASTER_{role}_{suffix}"
    for role in _ROLES
    for suffix in ("MODEL", "PROVIDER", "BASE_URL", "API_KEY_ENV")
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


def test_role_models_default_to_muninn_and_huginn(reload_with_env) -> None:
    """With no FORGEMASTER_*_MODEL env vars set, roles inherit MUNINN (Haiku for the implementer)."""
    runtime = reload_with_env()

    assert runtime._ROLE_TO_MODEL["orchestrator"] == runtime.MUNINN_MODEL
    assert runtime._ROLE_TO_MODEL["planner"] == runtime.MUNINN_MODEL
    assert runtime._ROLE_TO_MODEL["reviewer"] == runtime.MUNINN_MODEL
    assert runtime._ROLE_TO_MODEL["implementer"] == runtime.HUGINN_MODEL


def test_orchestrator_model_override_propagates(reload_with_env) -> None:
    """Setting FORGEMASTER_ORCHESTRATOR_MODEL swaps that role without touching the others."""
    runtime = reload_with_env({"FORGEMASTER_ORCHESTRATOR_MODEL": "claude-opus-test-alias"})

    assert runtime._ROLE_TO_MODEL["orchestrator"] == "claude-opus-test-alias"
    assert runtime._ROLE_TO_MODEL["planner"] == runtime.MUNINN_MODEL
    assert runtime._ROLE_TO_MODEL["reviewer"] == runtime.MUNINN_MODEL
    assert runtime._ROLE_TO_MODEL["implementer"] == runtime.HUGINN_MODEL


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


def test_implementer_override_reaches_the_anthropic_adapter(monkeypatch: pytest.MonkeyPatch, reload_with_env) -> None:
    """A model override on the implementer role is the model actually called."""
    runtime = reload_with_env({"FORGEMASTER_IMPLEMENTER_MODEL": "claude-test-override"})
    captured: dict = {}

    def _fake(model, prompt, max_tokens=4096, cached_system="", base_url="", api_key_env=""):
        captured["model"] = model
        return ("ok", 0, 0, 0)

    monkeypatch.setattr(runtime, "_call_anthropic", _fake)
    runtime._dispatch("implementer", "hello")
    assert captured["model"] == "claude-test-override"


def test_openai_compatible_role_dispatches_to_its_endpoint(monkeypatch: pytest.MonkeyPatch, reload_with_env) -> None:
    runtime = reload_with_env({
        "FORGEMASTER_IMPLEMENTER_PROVIDER": "openai_compatible",
        "FORGEMASTER_IMPLEMENTER_MODEL": "qwen-local",
        "FORGEMASTER_IMPLEMENTER_BASE_URL": "http://127.0.0.1:1234/v1",
    })
    captured: dict = {}

    def _fake(model, prompt, base_url, max_tokens=4096, cached_system="", api_key_env=""):
        captured.update(model=model, base_url=base_url, api_key_env=api_key_env)
        return ("ok", 0, 0, 0)

    monkeypatch.setattr(runtime, "_call_openai_compatible", _fake)
    runtime._dispatch("implementer", "hello")
    assert captured == {"model": "qwen-local", "base_url": "http://127.0.0.1:1234/v1", "api_key_env": ""}


def test_unknown_provider_fails_at_import(reload_with_env) -> None:
    with pytest.raises(ValueError, match="PROVIDER"):
        reload_with_env({"FORGEMASTER_REVIEWER_PROVIDER": "gemini"})


def test_openai_compatible_requires_base_url(reload_with_env) -> None:
    with pytest.raises(ValueError, match="BASE_URL"):
        reload_with_env({"FORGEMASTER_PLANNER_PROVIDER": "openai_compatible", "FORGEMASTER_PLANNER_MODEL": "m"})


def test_undispatchable_model_is_not_routed(reload_with_env) -> None:
    runtime = reload_with_env()
    assert runtime._is_dispatchable(runtime.HUGINN_MODEL)
    assert not runtime._is_dispatchable("gemini-2.5-flash")
