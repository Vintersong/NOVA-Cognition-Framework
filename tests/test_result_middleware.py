"""
isError marking for failed tool calls.

Every NOVA handler returns a JSON string, so the SDK reported every call as a
success and `isError` was only ever true for an unintended crash — the inverse
of what the spec intends. Server middleware now flips it for payloads that
report a failure, centrally, so all 41 tools behave consistently.
"""

from __future__ import annotations

import json

import pytest

from result_middleware import (
    FAILURE_STATUSES,
    _payload_failed,
    mark_failed_results,
    result_reports_failure,
)


def _text_result(payload, is_error: bool = False) -> dict:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


# ── classification ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", sorted(FAILURE_STATUSES))
def test_failure_statuses_are_detected(status):
    assert _payload_failed(json.dumps({"status": status, "message": "x"})) is True


@pytest.mark.parametrize("status", ["ok", "flushed", "loaded", "added", "removed",
                                    "skipped", "updated", "relation_added"])
def test_success_statuses_are_not_failures(status):
    """These are real NOVA success statuses — they must never trip isError."""
    assert _payload_failed(json.dumps({"status": status})) is False


@pytest.mark.parametrize("text", [
    "",                       # empty
    "not json at all",        # plain prose
    "[1, 2, 3]",              # JSON, but not an object
    '{"status": ',            # truncated JSON
    '{"total": 3}',           # object with no status
    '{"statuses": ["rejected"]}',   # similar key, not `status`
])
def test_non_envelope_text_is_not_a_failure(text):
    assert _payload_failed(text) is False


def test_status_must_be_the_envelope_status_not_nested():
    """A nested "status" must not be mistaken for the envelope's own."""
    assert _payload_failed(json.dumps({"results": [{"status": "rejected"}]})) is False


def test_result_reports_failure_scans_text_blocks():
    assert result_reports_failure(_text_result({"status": "rejected"})) is True
    assert result_reports_failure(_text_result({"status": "ok"})) is False
    assert result_reports_failure({"content": []}) is False
    assert result_reports_failure({}) is False
    assert result_reports_failure(None) is False


def test_non_text_content_is_ignored():
    result = {"content": [{"type": "image", "data": "..."}], "isError": False}
    assert result_reports_failure(result) is False


# ── middleware behaviour ─────────────────────────────────────────────────────

class _Ctx:
    def __init__(self, method):
        self.method = method


async def _call_next_returning(result):
    async def _inner(_ctx):
        return result
    return _inner


@pytest.mark.anyio
async def test_middleware_marks_failure(anyio_backend):
    result = _text_result({"status": "rejected", "code": "shard_not_found"})
    out = await mark_failed_results(_Ctx("tools/call"), await _call_next_returning(result))
    assert out["isError"] is True


@pytest.mark.anyio
async def test_middleware_leaves_success_alone(anyio_backend):
    result = _text_result({"status": "flushed", "session_id": "s1"})
    out = await mark_failed_results(_Ctx("tools/call"), await _call_next_returning(result))
    assert out["isError"] is False


@pytest.mark.anyio
async def test_middleware_ignores_other_methods(anyio_backend):
    """A resource read whose body happens to look like an envelope must not be
    reclassified — only tools/call carries isError."""
    result = _text_result({"status": "rejected"})
    out = await mark_failed_results(_Ctx("resources/read"), await _call_next_returning(result))
    assert out["isError"] is False


@pytest.mark.anyio
async def test_middleware_never_downgrades_an_existing_error(anyio_backend):
    result = _text_result({"status": "ok"}, is_error=True)
    out = await mark_failed_results(_Ctx("tools/call"), await _call_next_returning(result))
    assert out["isError"] is True


@pytest.mark.anyio
async def test_middleware_does_not_rewrite_the_body(anyio_backend):
    payload = {"status": "rejected", "code": "shard_not_found"}
    result = _text_result(payload)
    out = await mark_failed_results(_Ctx("tools/call"), await _call_next_returning(result))
    assert json.loads(out["content"][0]["text"]) == payload


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ── end to end through the real server ───────────────────────────────────────

@pytest.mark.anyio
async def test_isError_on_the_wire(anyio_backend):
    """The full protocol path: a client must be able to tell a refused call from
    a successful one without parsing NOVA's payload shape."""
    pytest.importorskip("mcp.server.mcpserver", reason="MCP SDK not installed")
    pytest.importorskip("sentence_transformers", reason="embedding stack not installed")

    from mcp import Client
    import nova_server

    async with Client(nova_server.mcp) as c:
        missing = await c.call_tool("nova_shard_get", {"params": {"shard_id": "does_not_exist"}})
        assert missing.is_error is True
        assert json.loads(missing.content[0].text)["code"] == "shard_not_found"

        missing_page = await c.call_tool("nova_wiki_get", {"params": {"slug": "no_such_page"}})
        assert missing_page.is_error is True

        ok = await c.call_tool("nova_session_list", {"params": {}})
        assert ok.is_error is False
