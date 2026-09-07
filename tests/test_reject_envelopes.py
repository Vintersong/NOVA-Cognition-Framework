"""
Error-envelope consistency across NOVA's tool modules.

NOVA previously returned three mutually incompatible error shapes, and a client
could not tell them apart:

  1. ``{"status": "rejected", "code": ...}``  — reject.py, used by one module
  2. ``{"status": "error", "message": ...}``  — the legacy shape
  3. ``{"error": ...}``                       — permission/gate denials, with no
                                                ``status`` key at all

These tests pin the two-shape contract that replaced it: every payload carries a
``status``. ``rejected`` means the tool refused for a known reason and the
envelope carries a machine-readable ``code``; ``error`` means something
unexpected broke. Shape 3 is gone.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from gate_helpers import permission_error
from permissions import denial_payload
from reject import _DEFAULTS, RejectCode, reject_dict, reject_payload


REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_DIR = REPO_ROOT / "mcp"

ENVELOPE_KEYS = {"status", "code", "message", "retryable", "hint"}


def test_every_reject_code_has_defaults():
    missing = [c for c in RejectCode if c not in _DEFAULTS]
    assert not missing, f"RejectCode values with no _DEFAULTS entry: {missing}"


@pytest.mark.parametrize("code", list(RejectCode))
def test_every_code_produces_a_well_formed_envelope(code):
    payload = reject_dict(code, "boom")
    assert ENVELOPE_KEYS <= set(payload)
    assert payload["status"] == "rejected"
    assert payload["code"] == code.value
    assert isinstance(payload["retryable"], bool)


def test_dict_and_string_forms_agree():
    kwargs = dict(target="s1", extra={"extra_key": 1})
    assert json.loads(reject_payload(RejectCode.SHARD_NOT_FOUND, "x", **kwargs)) == \
        reject_dict(RejectCode.SHARD_NOT_FOUND, "x", **kwargs)


def test_extra_cannot_clobber_reserved_keys():
    payload = reject_dict(
        RejectCode.INVALID_INPUT, "real message", extra={"status": "ok", "message": "spoofed"}
    )
    assert payload["status"] == "rejected"
    assert payload["message"] == "real message"


def test_permission_denial_is_a_typed_envelope():
    """Both denial helpers previously emitted a bare {"error": ...} with no
    status key. They must now agree and be machine-readable."""
    assert permission_error("nova_shard_forget") == denial_payload("nova_shard_forget")
    payload = json.loads(permission_error("nova_shard_forget"))
    assert payload["status"] == "rejected"
    assert payload["code"] == RejectCode.PERMISSION_DENIED.value
    assert payload["target"] == "nova_shard_forget"


def _dict_literal_keys(path: Path) -> list[tuple[int, set[str]]]:
    """Every dict literal in *path* whose keys are all plain strings."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = {k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        if keys and len(keys) == len(node.keys):
            out.append((node.lineno, keys))
    return out


# Modules that register MCP tools — i.e. whose return payloads reach a client.
# Internal libraries are excluded: an "error" key inside a telemetry event or an
# indexing stats record is a field, not an error envelope.
TOOL_MODULES = [
    "shard_tools.py", "graph_tools.py", "session_tools.py", "forgemaster_tools.py",
    "wiki_tools.py", "facts.py", "nidhogg.py", "evolve.py", "code_index.py",
    "huginn_tools.py", "calibrate.py", "external_retrieval.py",
    "gate_helpers.py", "permissions.py", "nova_server.py",
    "Gemini/gemini_mcp.py",
]


def test_no_tool_module_emits_a_statusless_error_payload():
    """The {"error": ...} shape carried no status key, so a client could not
    distinguish it from a successful result."""
    offenders = []
    for rel in TOOL_MODULES:
        path = MCP_DIR / rel
        assert path.exists(), f"TOOL_MODULES names a missing file: {rel}"
        for lineno, keys in _dict_literal_keys(path):
            if "error" in keys and "status" not in keys:
                offenders.append(f"mcp/{rel}:{lineno} {sorted(keys)}")
    assert not offenders, "status-less error payloads:\n  " + "\n  ".join(offenders)
