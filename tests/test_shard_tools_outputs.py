"""
test_shard_tools_outputs.py — the shard tools, end to end, over the wire.

Until now nothing exercised shard_tools' handlers at all: the registry tests
check metadata and the manifest test checks schemas, but no test ever created a
shard and read it back. That gap is exactly where the typed-output conversion
could break — a model is only as good as its agreement with the dict it
replaced, and pydantic validates on the way out.

Runs in a subprocess (``roundtrip_shard_tools.py``) with the data root pointed
at a tmpdir, because NOVA reads its paths once at import time. That also keeps
the real ``shards/`` untouched.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DRIVER = Path(__file__).resolve().parent / "roundtrip_shard_tools.py"


def run_driver(data_root, *args, **extra_env) -> dict:
    pytest.importorskip("mcp.server.mcpserver", reason="MCP SDK not installed")
    pytest.importorskip("sentence_transformers", reason="embedding stack not installed")

    env = {
        **os.environ,
        "NOVA_DATA_ROOT": str(data_root),
        # The SQLite shard index is the one data path that does *not* derive
        # from NOVA_DATA_ROOT (nova_shard_db.py pins it to the repo root), so
        # it has to be redirected separately or the test writes to the repo.
        "NOVA_SHARD_DB_FILE": str(data_root / "nova_shard_index.db"),
        **extra_env,
    }
    proc = subprocess.run(
        [sys.executable, str(DRIVER), *args],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=300,
    )
    marker = "@@REPORT@@"
    if marker not in proc.stdout:
        pytest.fail(f"driver produced no report\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    return json.loads(proc.stdout.split(marker, 1)[1])


@pytest.fixture(scope="module")
def report(tmp_path_factory) -> dict:
    return run_driver(tmp_path_factory.mktemp("nova_data"))


WIKI_TOOLS = [
    "nova_wiki_schema", "nova_wiki_ingest", "nova_wiki_query",
    "nova_wiki_get", "nova_wiki_list", "nova_wiki_lint",
]


@pytest.fixture(scope="module")
def denied_report(tmp_path_factory) -> dict:
    return run_driver(
        tmp_path_factory.mktemp("nova_denied"), "denied",
        NOVA_DENIED_TOOLS=",".join(WIKI_TOOLS),
    )


def call(report: dict, key: str) -> dict:
    return report["calls"][key]


# ── Every call is well-formed on the wire ────────────────────────────────────

def test_every_call_keeps_a_text_block(report):
    """The isError middleware parses the text block, so a typed return must not
    replace it with structured content alone."""
    for key, entry in report["calls"].items():
        assert entry["has_text"], f"{key} returned no text block"


def test_error_flag_matches_the_payload(report):
    for key, entry in report["calls"].items():
        payload = entry["payload"]
        rejected = isinstance(payload, dict) and payload.get("status") == "rejected"
        assert entry["is_error"] is rejected or entry["is_error"] is True, key


# ── The happy paths ──────────────────────────────────────────────────────────

def test_create_then_get_round_trips_the_shard(report):
    created = call(report, "nova_shard_create")["payload"]
    assert created["status"] == "created"

    shard = call(report, "nova_shard_get")["payload"]
    assert shard["shard_id"] == created["shard_id"]
    assert shard["guiding_question"] == created["guiding_question"]
    assert shard["conversation_history"][0]["user"] == "first turn"
    assert shard["meta_tags"]["theme"] == "testing"
    assert shard["meta_tags"]["confidence"] == 1.0
    # source="user_input" derives the authority chain at creation time.
    assert shard["meta_tags"]["epistemic_provenance"]["source_type"] == "authority_validated"


def test_update_appends_a_turn(report):
    updated = call(report, "nova_shard_update")["payload"]
    assert updated["status"] == "updated"
    assert updated["total_entries"] == 2

    body = call(report, "nova_shard_get_full")["payload"]["body"]
    assert [t["user"] for t in body] == ["first turn", "second"]


def test_validate_records_the_authority_chain(report):
    validated = call(report, "nova_shard_validate")["payload"]
    assert validated["status"] == "validated"
    record = validated["epistemic_provenance"]
    assert record["source_type"] == "externally_published"
    assert record["validator"] == "roundtrip"
    assert record["events"], "a validation event should have been appended"


def test_validate_refuses_to_downgrade_the_source_type(report):
    """The shard is already authority_validated; a peer_validated event would
    quietly lower its authority, which is only legitimate under a supersession."""
    entry = call(report, "validate_downgrade")
    assert entry["is_error"] is True
    assert entry["payload"]["code"] == "invalid_input"
    assert "downgrade source_type" in entry["payload"]["message"]


def test_search_ranks_the_shard(report):
    results = call(report, "nova_shard_search")["payload"]
    assert results["query"] == "roundtrip behave"
    assert results["results"][0]["shard_id"] == "testing_reflection"
    assert results["results"][0]["weighted_score"] > 0


def test_browse_envelope_keeps_its_version_key_and_compact_rows(report):
    """`_v` is an alias, and compact single-letter keys are the point of the
    browse projection — a model that renamed either would be a wire break."""
    payload = call(report, "nova_shard_index")["payload"]
    assert payload["_v"] == 3
    assert payload["tool"] == "nova_shard_index"
    assert payload["themes"] is None
    row = payload["shards"][0]
    assert set(row) >= {"id", "d", "t", "c", "created", "n"}
    assert row["id"] == "testing_reflection"


def test_grouped_browse_returns_themes_not_shards(report):
    payload = call(report, "summary_themed")["payload"]
    assert payload["shards"] is None
    assert payload["themes"]["testing"]["count"] == 1
    # `s` (the synopsis) is what separates summary from index.
    assert payload["themes"]["testing"]["shards"][0]["s"]


def test_legacy_dump_returns_whole_shards(report):
    payload = call(report, "nova_shard_list")["payload"]
    assert payload["deprecated"] is True
    assert payload["shards"][0]["conversation_history"]


def test_state_query_decodes_the_state_vector(report):
    payload = call(report, "nova_shard_query_state")["payload"]
    hit = next(r for r in payload["results"] if r["shard_id"] == "testing_reflection")
    assert hit["state"]["epistemic"] == 2
    assert 0.0 <= hit["state"]["confidence"] <= 1.0


def test_merge_creates_a_meta_shard(report):
    payload = call(report, "nova_shard_merge")["payload"]
    assert payload["status"] == "merged"
    assert payload["sources"] == ["testing_reflection", "testing_reflection_1"]


def test_missing_shard_rejects_with_a_typed_code(report):
    entry = call(report, "missing")
    assert entry["is_error"] is True
    assert entry["payload"]["code"] == "shard_not_found"
    assert entry["payload"]["target"] == "does_not_exist"


# ── The gate ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("tool", ["nova_shard_archive", "nova_shard_forget"])
def test_destructive_tools_refuse_without_an_approval_channel(report, tool):
    """The client in the driver declares no elicitation capability, so there is
    nobody to ask. A destructive tool must then refuse rather than proceed."""
    entry = call(report, tool)
    assert entry["is_error"] is True
    assert entry["payload"]["code"] == "gate_denied"
    assert entry["payload"]["retryable"] is True


# ── The one place serialisation is load-bearing ──────────────────────────────

def test_interact_token_accounting_follows_the_persisted_transcript(report):
    """`nova_shard_interact` serialises its payload mid-body: UsageSummary counts
    words in that string and the session store persists it verbatim. Returning a
    model must not change either, so the handler keeps its own json.dumps."""
    session = report["session"]
    user_msg, assistant_msg = session["messages"]

    assert user_msg == session["user_message"]
    assert session["input_tokens"] == len(user_msg.split())
    assert session["output_tokens"] == len(assistant_msg.split())
    assert session["total_tokens"] == session["input_tokens"] + session["output_tokens"]


def test_interact_transcript_is_the_payload_the_client_received(report):
    """Same data both ways — only the key ordering differs, because the
    transcript is the raw payload and the wire copy went through the model."""
    stored = json.loads(report["session"]["messages"][1])
    wire = call(report, "nova_shard_interact")["payload"]

    assert stored["status"] == wire["status"] == "loaded"
    assert stored["inferred"] is wire["inferred"] is False
    assert [s["shard_id"] for s in stored["shards"]] == [s["shard_id"] for s in wire["shards"]]
    assert stored["shards"][0]["fragments"] == wire["shards"][0]["fragments"]


# ── Wiki ─────────────────────────────────────────────────────────────────────

def test_wiki_schema_add_get_remove(report):
    assert call(report, "wiki_schema_empty")["payload"] == {"page_count": 0, "pages": []}

    added = call(report, "wiki_add")["payload"]
    assert added["status"] == "added"
    assert added["total"] == 1

    schema = call(report, "wiki_schema")["payload"]
    assert schema["pages"][0]["slug"] == "roundtrip"
    assert schema["pages"][0]["category"] == "testing"

    removed = call(report, "wiki_remove")["payload"]
    assert removed["status"] == "removed"
    assert removed["total"] == 0


@pytest.mark.parametrize("key,code", [
    ("wiki_add_duplicate", "duplicate"),
    ("wiki_remove_again", "wiki_page_not_found"),
    ("wiki_missing", "wiki_page_not_found"),
])
def test_wiki_rejections_are_typed(report, key, code):
    entry = call(report, key)
    assert entry["is_error"] is True
    assert entry["payload"]["code"] == code


def test_wiki_ingest_reports_the_routing_result(report):
    """With no CLAUDE_API_KEY the routing pass returns nothing, which is the
    'no relevant pages' branch — still a WikiIngested, not a refusal."""
    payload = call(report, "wiki_ingest")["payload"]
    assert payload["source_name"] == "roundtrip.txt"
    assert payload["routed_slugs"] == []
    assert payload["synthesized"] == []


def test_wiki_list_and_lint_on_an_empty_wiki(report):
    assert call(report, "wiki_list")["payload"] == {
        "total": 0, "category": "all", "pages": [],
    }
    lint = call(report, "wiki_lint")["payload"]
    assert lint["total_pages"] == 0
    assert lint["deep_lint"] is None


def test_wiki_tools_honour_the_permission_context(denied_report):
    """The six wiki tools were the only ones that never consulted the permission
    context, so NOVA_DENIED_TOOLS silently did nothing for them."""
    assert set(denied_report["calls"]) == set(WIKI_TOOLS)
    for name, entry in denied_report["calls"].items():
        assert entry["is_error"] is True, name
        assert entry["payload"]["code"] == "permission_denied", name
        assert entry["payload"]["target"] == name
