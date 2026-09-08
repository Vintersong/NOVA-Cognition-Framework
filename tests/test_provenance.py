"""
test_provenance.py — unit tests for the epistemic provenance record.

Run: cd mcp && python -m pytest test_provenance.py -v

Covers the provenance taxonomy/helpers (provenance.py), round-trip
serialization of the record (shard_format.py), and the input-schema guards
(schemas.py). Does not exercise the async MCP handler — its logic lives in
provenance.apply_validation_event, which is tested directly here.
"""

import pytest

from provenance import (
    AUTHORITY_RANK,
    MAX_EVENTS,
    SOURCE_TYPES,
    apply_validation_event,
    build_initial,
    default_provenance,
    ensure_provenance,
)
from shard_format import md_to_shard, shard_to_md


# ── default_provenance derivation ─────────────────────────────────────────────

@pytest.mark.parametrize("source,expected_type,expected_mech", [
    ("user_input",        "authority_validated",  "user_assertion"),
    ("external_doc",      "externally_published", "external_publication"),
    ("corroborated_by",   "peer_validated",       "convergence_evidence"),
    ("session_extracted", "self_inferred",        "self_inference"),
    ("agent_inference",   "self_inferred",        "self_inference"),
    ("something_unknown", "self_inferred",        "self_inference"),
])
def test_default_provenance_maps_source(source, expected_type, expected_mech):
    rec = default_provenance({"source": source})
    assert rec["source_type"] == expected_type
    assert rec["mechanism"] == expected_mech
    assert rec["confidence_delta"] == 0.0
    assert rec["superseded"] is False
    assert rec["events"] == []


def test_default_provenance_mirrors_superseded_by():
    rec = default_provenance({"source": "agent_inference", "superseded_by": "shard_x"})
    assert rec["superseded"] is True
    assert rec["superseded_by"] == "shard_x"


def test_user_input_sets_validator():
    assert default_provenance({"source": "user_input"})["validator"] == "user"


def test_authority_rank_is_ordered():
    assert AUTHORITY_RANK["self_inferred"] < AUTHORITY_RANK["peer_validated"]
    assert AUTHORITY_RANK["authority_validated"] < AUTHORITY_RANK["externally_published"]
    assert set(AUTHORITY_RANK) == set(SOURCE_TYPES)


# ── build_initial ─────────────────────────────────────────────────────────────

def test_build_initial_overlays_explicit_fields():
    rec = build_initial(
        "agent_inference",
        source_type="authority_validated",
        validator="andrei",
        mechanism="rubric",
    )
    assert rec["source_type"] == "authority_validated"
    assert rec["validator"] == "andrei"
    assert rec["mechanism"] == "rubric"


def test_build_initial_defaults_from_source():
    rec = build_initial("external_doc")
    assert rec["source_type"] == "externally_published"


def test_build_initial_rejects_bad_source_type():
    with pytest.raises(ValueError):
        build_initial("agent_inference", source_type="nonsense")


# ── ensure_provenance idempotency ─────────────────────────────────────────────

def test_ensure_provenance_backfills_then_is_stable():
    shard = {"meta_tags": {"source": "user_input"}}
    first = ensure_provenance(shard)
    assert shard["meta_tags"]["epistemic_provenance"] is first
    # second call returns the same object, unchanged
    first["validator"] = "mutated"
    second = ensure_provenance(shard)
    assert second is first
    assert second["validator"] == "mutated"


# ── apply_validation_event ────────────────────────────────────────────────────

def test_validation_raises_confidence_via_corroboration():
    shard = {"meta_tags": {"source": "agent_inference", "confidence": 0.5}}
    rec = apply_validation_event(
        shard,
        source_type="authority_validated",
        validator="andrei",
        mechanism="rubric",
        confidence_delta=0.1,
    )
    assert shard["meta_tags"]["confidence"] == pytest.approx(0.6)
    assert rec["source_type"] == "authority_validated"
    assert rec["validator"] == "andrei"
    assert rec["mechanism"] == "rubric"
    assert rec["confidence_delta"] == pytest.approx(0.1)
    assert rec["validated_at"] is not None
    assert len(rec["events"]) == 1
    assert rec["events"][0]["delta"] == pytest.approx(0.1)


def test_confidence_delta_accumulates_and_caps():
    shard = {"meta_tags": {"source": "agent_inference", "confidence": 0.95}}
    apply_validation_event(shard, source_type="peer_validated", confidence_delta=0.1)
    rec = apply_validation_event(shard, source_type="peer_validated", confidence_delta=0.1)
    assert shard["meta_tags"]["confidence"] == pytest.approx(1.0)  # capped
    assert rec["confidence_delta"] == pytest.approx(0.2)  # cumulative delta, uncapped


def test_negative_delta_rejected():
    shard = {"meta_tags": {"source": "agent_inference", "confidence": 0.5}}
    with pytest.raises(ValueError):
        apply_validation_event(shard, source_type="peer_validated", confidence_delta=-0.1)


def test_events_log_is_capped():
    shard = {"meta_tags": {"source": "agent_inference", "confidence": 0.5}}
    for _ in range(MAX_EVENTS + 3):
        apply_validation_event(shard, source_type="peer_validated")
    rec = shard["meta_tags"]["epistemic_provenance"]
    assert len(rec["events"]) == MAX_EVENTS


def test_supersession_sets_flags_without_lowering_confidence():
    shard = {"meta_tags": {"source": "agent_inference", "confidence": 0.7}}
    rec = apply_validation_event(
        shard,
        source_type="externally_published",
        superseded=True,
        superseded_by="shard_authority",
    )
    assert rec["superseded"] is True
    assert rec["superseded_by"] == "shard_authority"
    assert shard["meta_tags"]["superseded_by"] == "shard_authority"
    # confidence untouched (no delta) — demotion handled by decay/gating
    assert shard["meta_tags"]["confidence"] == pytest.approx(0.7)


def test_supersession_requires_higher_authority():
    # Current record is authority_validated; a peer_validated source cannot supersede it.
    shard = {"meta_tags": {"source": "user_input", "confidence": 0.9}}
    ensure_provenance(shard)  # source_type = authority_validated
    with pytest.raises(ValueError):
        apply_validation_event(
            shard,
            source_type="peer_validated",
            superseded=True,
            superseded_by="weak_shard",
        )


def test_same_rank_event_is_allowed():
    shard = {"meta_tags": {"source": "user_input", "confidence": 0.9}}
    ensure_provenance(shard)  # source_type = authority_validated
    rec = apply_validation_event(
        shard, source_type="authority_validated", validator="andrei"
    )
    assert rec["source_type"] == "authority_validated"
    assert rec["validator"] == "andrei"


def test_higher_rank_event_is_allowed():
    shard = {"meta_tags": {"source": "agent_inference", "confidence": 0.5}}
    ensure_provenance(shard)  # source_type = self_inferred
    rec = apply_validation_event(shard, source_type="externally_published")
    assert rec["source_type"] == "externally_published"
    assert AUTHORITY_RANK["externally_published"] > AUTHORITY_RANK["self_inferred"]


def test_downgrade_without_supersession_rejected():
    # An authority_validated record must not be silently overwritten as
    # self_inferred by a plain (non-superseding) validation event.
    shard = {"meta_tags": {"source": "user_input", "confidence": 0.9}}
    ensure_provenance(shard)  # source_type = authority_validated
    with pytest.raises(ValueError, match="cannot downgrade source_type"):
        apply_validation_event(shard, source_type="self_inferred")
    assert shard["meta_tags"]["epistemic_provenance"]["source_type"] == "authority_validated"


def test_downgrade_with_supersession_still_rejected():
    # superseded=True routes past the downgrade guard, but the supersession
    # rank check still requires the NEW type to outrank the current one.
    shard = {"meta_tags": {"source": "user_input", "confidence": 0.9}}
    ensure_provenance(shard)  # source_type = authority_validated
    with pytest.raises(ValueError, match="does not outrank"):
        apply_validation_event(
            shard,
            source_type="self_inferred",
            superseded=True,
            superseded_by="weak_shard",
        )
    rec = shard["meta_tags"]["epistemic_provenance"]
    assert rec["source_type"] == "authority_validated"
    assert rec["superseded"] is False


def test_supersession_requires_superseded_by():
    shard = {"meta_tags": {"source": "agent_inference", "confidence": 0.5}}
    with pytest.raises(ValueError):
        apply_validation_event(shard, source_type="peer_validated", superseded=True)


# ── serialization round-trip ──────────────────────────────────────────────────

def _shard_with_provenance() -> dict:
    return {
        "shard_id": "test_001",
        "guiding_question": "Does provenance round-trip?",
        "tags": ["test"],
        "conversation_history": [{"user": "hi", "ai": "hello"}],
        "meta_tags": {
            "confidence": 0.8,
            "theme": "general",
            "intent": "reflection",
            "source": "agent_inference",
            "last_used": "2026-06-01T00:00:00",
            "usage_count": 1,
            "enrichment_status": "enriched",
            "provenance": "en|WEIRD|LLM",
            "epistemic_provenance": {
                "source_type": "authority_validated",
                "validator": "andrei",
                "mechanism": "rubric",
                "confidence_delta": 0.1,
                "validated_at": "2026-06-01T00:00:00",
                "superseded": False,
                "superseded_by": None,
                "events": [
                    {"source_type": "authority_validated", "validator": "andrei",
                     "mechanism": "rubric", "delta": 0.1, "at": "2026-06-01T00:00:00"},
                ],
            },
        },
        "context": {"summary": "", "topics": []},
    }


def test_record_round_trips_through_markdown():
    data = _shard_with_provenance()
    restored = md_to_shard(shard_to_md(data), "test_001")
    assert restored["meta_tags"]["epistemic_provenance"] == \
        data["meta_tags"]["epistemic_provenance"]


def test_absent_record_serializes_as_absent():
    data = _shard_with_provenance()
    del data["meta_tags"]["epistemic_provenance"]
    md = shard_to_md(data)
    assert "epistemic_provenance" not in md
    restored = md_to_shard(md, "test_001")
    assert "epistemic_provenance" not in restored["meta_tags"]


# ── schema guards ─────────────────────────────────────────────────────────────

def test_schema_rejects_superseded_without_target():
    from schemas import ShardValidateInput
    with pytest.raises(Exception):
        ShardValidateInput(shard_id="s", source_type="peer_validated", superseded=True)


def test_schema_rejects_negative_delta():
    from schemas import ShardValidateInput
    with pytest.raises(Exception):
        ShardValidateInput(shard_id="s", source_type="peer_validated", confidence_delta=-0.1)


def test_create_schema_accepts_provenance_params():
    from schemas import ShardCreateInput
    inp = ShardCreateInput(
        guiding_question="q",
        source="user_input",
        prov_source_type="authority_validated",
        prov_validator="andrei",
        prov_mechanism="rubric",
    )
    assert inp.prov_source_type == "authority_validated"
