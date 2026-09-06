"""
provenance.py — first-class epistemic provenance records for NOVA shards.

A shard's ``confidence`` float says *how much* to trust a memory; it cannot say
*why*. The epistemic provenance record adds the chain of custody: which kind of
source stands behind the memory (``source_type``), who validated it
(``validator``), through what mechanism (``mechanism``), how much confidence a
validation event contributed (``confidence_delta``), and whether a
higher-authority source has since superseded it.

The record lives at ``meta_tags.epistemic_provenance`` — a nested dict that
sits *alongside* the existing ``source`` origin tag and ``superseded_by``
field, never replacing them. ``source`` answers "where did the content come
from"; ``source_type`` answers "what level of epistemic authority stands
behind it". The two are orthogonal, and ``source_type`` is derived from
``source`` whenever it isn't given explicitly.

Confidence may only ever *rise* through ``maintenance.apply_confidence_corroboration``
(see maintenance.py:29). This module honours that invariant: a positive
``confidence_delta`` routes through that path; supersession sets flags but never
lowers confidence directly (decay and state-gating handle demotion).
"""

from __future__ import annotations

from typing import Any

from maintenance import apply_confidence_corroboration
from timeutils import now_utc

# ═══════════════════════════════════════════════════════════
# TAXONOMY
# ═══════════════════════════════════════════════════════════

# Ordered weakest → strongest. The order is the authority ranking.
SOURCE_TYPES: tuple[str, ...] = (
    "self_inferred",
    "peer_validated",
    "authority_validated",
    "externally_published",
)

# Authority rank: higher number outranks lower. Used to decide whether a
# superseding memory is genuinely *higher authority* than the one it replaces.
AUTHORITY_RANK: dict[str, int] = {st: i for i, st in enumerate(SOURCE_TYPES)}

# Maximum validation events retained in the append-log (keeps frontmatter terse).
MAX_EVENTS = 5

# Default derivation from the legacy ``meta_tags.source`` origin tag.
# Each entry: source -> (source_type, mechanism, validator)
_SOURCE_DEFAULTS: dict[str, tuple[str, str, str | None]] = {
    "user_input":       ("authority_validated",   "user_assertion",       "user"),
    "external_doc":     ("externally_published",  "external_publication", None),
    "corroborated_by":  ("peer_validated",        "convergence_evidence", None),
    "session_extracted": ("self_inferred",        "self_inference",       None),
    "agent_inference":  ("self_inferred",         "self_inference",       None),
}


# ═══════════════════════════════════════════════════════════
# RECORD CONSTRUCTION
# ═══════════════════════════════════════════════════════════

def _blank_record() -> dict[str, Any]:
    return {
        "source_type": "self_inferred",
        "validator": None,
        "mechanism": "self_inference",
        "confidence_delta": 0.0,
        "validated_at": None,
        "superseded": False,
        "superseded_by": None,
        "events": [],
    }


def default_provenance(meta_tags: dict) -> dict[str, Any]:
    """Build a provenance record from a shard's existing meta_tags.

    Reads the legacy ``source`` origin tag to pick a sensible ``source_type``,
    ``mechanism`` and ``validator``, and mirrors ``meta_tags.superseded_by``
    into the record's supersession flags. Pure — does not mutate the input.
    """
    source = meta_tags.get("source", "agent_inference")
    source_type, mechanism, validator = _SOURCE_DEFAULTS.get(
        source, ("self_inferred", "self_inference", None)
    )
    record = _blank_record()
    record["source_type"] = source_type
    record["mechanism"] = mechanism
    record["validator"] = validator

    superseded_by = meta_tags.get("superseded_by")
    if superseded_by:
        record["superseded"] = True
        record["superseded_by"] = superseded_by

    return record


def build_initial(
    source: str,
    *,
    source_type: str | None = None,
    validator: str | None = None,
    mechanism: str | None = None,
) -> dict[str, Any]:
    """Build the initial provenance record stamped at shard creation.

    Starts from the default derived from ``source``, then overlays any
    explicitly-provided fields. No confidence_delta at creation — confidence
    starts at 1.0 and deltas are a validation-time concept.
    """
    record = default_provenance({"source": source})
    if source_type is not None:
        if source_type not in AUTHORITY_RANK:
            raise ValueError(f"unknown source_type '{source_type}'")
        record["source_type"] = source_type
    if validator is not None:
        record["validator"] = validator
    if mechanism is not None:
        record["mechanism"] = mechanism
    return record


def ensure_provenance(shard_data: dict) -> dict[str, Any]:
    """Return the shard's provenance record, backfilling a default in place.

    Idempotent: an existing record is returned untouched. Used by the validate
    tool and the backfill utility — *not* by serialization, which round-trips
    faithfully and never fabricates a record.
    """
    meta = shard_data.setdefault("meta_tags", {})
    record = meta.get("epistemic_provenance")
    if not isinstance(record, dict):
        record = default_provenance(meta)
        meta["epistemic_provenance"] = record
    return record


# ═══════════════════════════════════════════════════════════
# VALIDATION EVENTS
# ═══════════════════════════════════════════════════════════

def apply_validation_event(
    shard_data: dict,
    *,
    source_type: str,
    validator: str | None = None,
    mechanism: str = "",
    confidence_delta: float = 0.0,
    superseded: bool = False,
    superseded_by: str | None = None,
) -> dict[str, Any]:
    """Record a validation event on a shard's provenance record.

    - Updates source_type / validator / mechanism and stamps ``validated_at``.
    - A positive ``confidence_delta`` routes through
      ``apply_confidence_corroboration`` (the only sanctioned confidence-raise
      path) and accumulates into ``confidence_delta``.
    - Outside of an explicit supersession, ``source_type`` may only move to an
      equal-or-higher authority rank; a downgrade raises ``ValueError`` so a
      plain validation event cannot silently weaken a shard's authority.
    - Supersession sets the record's flags and ``meta_tags.superseded_by``; it
      never lowers confidence directly. If both ``superseded_by`` and the prior
      ``source_type`` are known, the superseding source must outrank the
      current one.
    - Appends a compact event to the capped ``events`` log.

    Mutates ``shard_data`` in place and returns the updated record.
    """
    if source_type not in AUTHORITY_RANK:
        raise ValueError(f"unknown source_type '{source_type}'")
    if confidence_delta < 0:
        raise ValueError("confidence_delta must be non-negative; confidence may only rise via corroboration")
    if superseded and not superseded_by:
        raise ValueError("superseded_by is required when superseded=True")

    meta = shard_data.setdefault("meta_tags", {})
    record = ensure_provenance(shard_data)
    prior_type = record.get("source_type", "self_inferred")

    if AUTHORITY_RANK.get(source_type, 0) < AUTHORITY_RANK.get(prior_type, 0) and not superseded:
        raise ValueError(
            f"cannot downgrade source_type from '{prior_type}' to '{source_type}' "
            f"outside of an explicit supersession"
        )

    if superseded and superseded_by:
        if AUTHORITY_RANK.get(source_type, 0) < AUTHORITY_RANK.get(prior_type, 0):
            raise ValueError(
                f"superseding source_type '{source_type}' does not outrank current '{prior_type}'"
            )
        record["superseded"] = True
        record["superseded_by"] = superseded_by
        meta["superseded_by"] = superseded_by

    record["source_type"] = source_type
    if validator is not None:
        record["validator"] = validator
    if mechanism:
        record["mechanism"] = mechanism

    now = now_utc().isoformat()
    record["validated_at"] = now

    if confidence_delta > 0:
        apply_confidence_corroboration(shard_data, confidence_delta)
        record["confidence_delta"] = round(
            float(record.get("confidence_delta", 0.0)) + confidence_delta, 4
        )

    event = {
        "source_type": source_type,
        "validator": validator,
        "mechanism": mechanism or record["mechanism"],
        "delta": round(float(confidence_delta), 4),
        "at": now,
    }
    events = record.setdefault("events", [])
    events.append(event)
    if len(events) > MAX_EVENTS:
        del events[: len(events) - MAX_EVENTS]

    return record
