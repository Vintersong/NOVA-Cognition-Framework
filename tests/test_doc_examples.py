"""
Documentation examples must match the real schemas.

`mcp/ONBOARDING.md` is the flow an assistant runs on a fresh install, and its
`nova_shard_create` example had drifted: it passed `user_message` and
`ai_response`, which are not fields of `ShardCreateInput`, to a model that sets
`extra='forbid'`. The very first call on a new install raised a validation error.

Nothing checked it, because it is prose. This does.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from schemas import ShardCreateInput

REPO_ROOT = Path(__file__).resolve().parent.parent
ONBOARDING = REPO_ROOT / "mcp" / "ONBOARDING.md"


def _extract_create_params() -> dict:
    """Pull the params dict out of ONBOARDING's nova_shard_create example."""
    text = ONBOARDING.read_text(encoding="utf-8")
    match = re.search(
        r"nova_shard_create\(params=(\{.*?\})\s*\)", text, re.DOTALL
    )
    assert match, "ONBOARDING.md no longer contains a nova_shard_create(params={...}) example"
    return ast.literal_eval(match.group(1))


def test_onboarding_example_is_a_params_object():
    """Every NOVA tool takes a single `params` object — a flat-kwargs example
    would not match the published input schema."""
    params = _extract_create_params()
    assert isinstance(params, dict) and params


def test_onboarding_example_uses_only_real_fields():
    unknown = set(_extract_create_params()) - set(ShardCreateInput.model_fields)
    assert not unknown, f"ONBOARDING.md passes fields that do not exist: {sorted(unknown)}"


def test_onboarding_example_validates():
    """The end-to-end check: the documented call must actually construct."""
    model = ShardCreateInput(**_extract_create_params())
    assert model.guiding_question


def test_unknown_fields_really_are_rejected():
    """Guards the guard: if extra='forbid' were ever relaxed, the tests above
    would stop being meaningful."""
    with pytest.raises(ValidationError):
        ShardCreateInput(guiding_question="q", not_a_real_field="x")
