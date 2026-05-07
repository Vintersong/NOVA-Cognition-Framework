"""
timeutils.py — ISO-8601 parsing helpers that always return tz-aware UTC.

NOVA writes timestamps with a mix of `datetime.now()` (naive local) and
`datetime.now(timezone.utc).isoformat()` (aware UTC). When a stored aware
timestamp later gets compared against a fresh naive `datetime.now()`,
Python raises TypeError. The legacy code swallows that in `try/except`,
silently treating "this gate failed to evaluate" as "gate passed" — which
lets expired or future-dated shards leak into retrieval.

`parse_iso` normalizes any incoming ISO string to aware UTC, returning
None on invalid input. `now_utc` returns aware UTC for the comparison side.
"""

from __future__ import annotations

from datetime import datetime, timezone


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 string and return a tz-aware UTC datetime.

    Returns None on empty / non-string / unparseable input.
    Naive timestamps are assumed to be UTC (matches how callers used to
    compare against naive `datetime.now()`).
    """
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def now_utc() -> datetime:
    """Aware UTC `now()`. Use this on the other side of any parse_iso compare."""
    return datetime.now(timezone.utc)
