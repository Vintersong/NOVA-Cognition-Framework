"""
nova_calibrate_routing input-schema wiring.

`_run_calibration` reads `params.include_replay_divergence` and
`params.include_forgemaster`, but both fields had been declared on
`HuginnCandidatesInput` instead of `CalibrateRoutingInput` — a stray blank line
put them under the wrong class. The effect was twofold: nova_calibrate_routing
raised AttributeError on every call, and nova_huginn_candidates advertised two
booleans its handler never reads (and that `extra='forbid'` made unusable).
"""

from __future__ import annotations

import pytest

import calibrate
from schemas import CalibrateRoutingInput, HuginnCandidatesInput


CALIBRATION_FLAGS = ("include_forgemaster", "include_replay_divergence")


@pytest.mark.parametrize("field", CALIBRATION_FLAGS)
def test_calibration_flags_live_on_the_calibrate_model(field):
    assert field in CalibrateRoutingInput.model_fields


@pytest.mark.parametrize("field", CALIBRATION_FLAGS)
def test_calibration_flags_are_not_advertised_by_huginn(field):
    assert field not in HuginnCandidatesInput.model_fields


def test_huginn_rejects_the_calibration_flags():
    """extra='forbid' means a client that believed the old schema got an error."""
    with pytest.raises(Exception):
        HuginnCandidatesInput(query="x", include_forgemaster=True)


def test_run_calibration_reads_every_field_it_declares(tmp_path, monkeypatch):
    """End-to-end guard: the runner must not AttributeError on its own params.
    Empty log -> the analysis sections are empty, but all three must be present."""
    import config
    monkeypatch.setattr(config, "USAGE_LOG_FILE", str(tmp_path / "absent.jsonl"))

    result = calibrate._run_calibration(CalibrateRoutingInput())

    assert "huginn" in result
    assert "replay_divergence" in result
    assert "forgemaster" in result


def test_calibration_flags_gate_their_sections(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "USAGE_LOG_FILE", str(tmp_path / "absent.jsonl"))

    result = calibrate._run_calibration(
        CalibrateRoutingInput(include_forgemaster=False, include_replay_divergence=False)
    )

    assert "huginn" in result
    assert "replay_divergence" not in result
    assert "forgemaster" not in result
