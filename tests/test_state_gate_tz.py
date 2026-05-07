from __future__ import annotations

from datetime import datetime, timedelta, timezone

import store
import timeutils


def _entry(*, start: str | None = None, end: str | None = None) -> dict:
    meta: dict = {}
    if start or end:
        window: dict = {}
        if start is not None:
            window["start"] = start
        if end is not None:
            window["end"] = end
        meta["validity_window"] = window
    return {"meta": meta}


def test_passes_state_gate_aware_end_in_past_excludes() -> None:
    past = (timeutils.now_utc() - timedelta(days=1)).isoformat()
    assert store.passes_state_gate(_entry(end=past)) is False


def test_passes_state_gate_aware_end_in_future_admits() -> None:
    future = (timeutils.now_utc() + timedelta(days=1)).isoformat()
    assert store.passes_state_gate(_entry(end=future)) is True


def test_passes_state_gate_aware_start_in_future_excludes() -> None:
    future = (timeutils.now_utc() + timedelta(days=1)).isoformat()
    assert store.passes_state_gate(_entry(start=future)) is False


def test_passes_state_gate_naive_iso_treated_as_utc() -> None:
    """Legacy timestamps written by datetime.now().isoformat() (naive) must
    not raise TypeError when compared and must be treated as UTC."""
    past_naive = (datetime.now(timezone.utc) - timedelta(days=2)).replace(tzinfo=None).isoformat()
    assert store.passes_state_gate(_entry(end=past_naive)) is False


def test_passes_state_gate_z_suffix_supported() -> None:
    """Timestamps ending in 'Z' (common for stdlib + many JSON encoders)
    must parse without raising."""
    future_z = (timeutils.now_utc() + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert store.passes_state_gate(_entry(end=future_z)) is True


def test_passes_state_gate_invalid_timestamp_admits() -> None:
    """Garbage timestamp parses to None, gate falls through (no constraint)."""
    assert store.passes_state_gate(_entry(end="not-a-date")) is True


def test_parse_iso_returns_aware_utc() -> None:
    naive = timeutils.parse_iso("2025-01-01T12:00:00")
    aware = timeutils.parse_iso("2025-01-01T12:00:00+02:00")
    assert naive is not None and naive.tzinfo == timezone.utc
    assert aware is not None and aware.tzinfo == timezone.utc
    # +02:00 → 10:00 UTC
    assert aware.hour == 10


def test_parse_iso_handles_empty_and_invalid() -> None:
    assert timeutils.parse_iso(None) is None
    assert timeutils.parse_iso("") is None
    assert timeutils.parse_iso("   ") is None
    assert timeutils.parse_iso("garbage") is None
