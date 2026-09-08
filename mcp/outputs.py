"""
outputs.py — Pydantic output models for NOVA's MCP tools.

Every handler used to be annotated ``-> str`` and return ``json.dumps(...)``.
The SDK generates an ``outputSchema`` from the return annotation, so that
published a degenerate ``{"result": string}`` for all 41 tools and put the real
payload into ``structuredContent`` as an *escaped JSON string*. A client got no
usable schema and had to re-parse text it had already been handed.

Annotating a handler with a model here fixes both: the SDK derives a real
``outputSchema`` from the model and emits real ``structuredContent``. It still
emits the text block as well, so ``result_middleware`` keeps working unchanged.

## The union convention

A handler can also refuse, so its annotation is
``ReturnType = Success | RejectPayload``. That publishes a schema describing
both shapes, discriminated by ``status``, and keeps the refusal envelope as one
shared model (``reject.RejectPayload``) rather than 41 copies of the same
fields. The cost is that the SDK nests ``structuredContent`` under a ``result``
key for union returns; schema fidelity is worth that.

## Field naming

Where an existing payload already had a shape, these models reproduce it
exactly rather than tidying it — the browse rows keep their single-letter keys
(``d``/``t``/``c``/``n``), which are a deliberate token-saving convention, and
``_v`` stays as the envelope version. Renaming would be a breaking change for
no gain.
"""

from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from reject import RejectPayload

__all__ = [
    "NovaOutput",
    "ErrorPayload",
    "RejectPayload",
    "Failure",
    "TokenTotals",
    "StateVector",
    "BrowseRow",
    "BrowseEnvelope",
    "SessionFlushed",
    "SessionLoaded",
    "SessionList",
    "SessionFlushResult",
    "SessionLoadResult",
    "SessionListResult",
]


class NovaOutput(BaseModel):
    """Base for every success payload.

    ``extra="allow"`` on purpose: several payloads carry a key only under some
    condition (``nova_graph_relate``'s ``confidence_after_corroboration``,
    ``nova_wiki_ingest``'s ``message``), and some are passthroughs from another
    module whose shape is not owned here. Declaring the known fields gives a
    useful schema without claiming the payload is closed.
    """

    model_config = ConfigDict(extra="allow")


class ErrorPayload(NovaOutput):
    """Something unexpected broke.

    The second of NOVA's two failure shapes. ``rejected`` means the tool refused
    for a known reason and says which; ``error`` means an exception escaped.
    Both set ``isError`` on the wire (see ``result_middleware``).
    """

    status: Literal["error"] = "error"
    message: str = Field(description="The exception text.")


#: Either failure shape. Every handler's return annotation is
#: ``Success | Failure``, so the published schema covers refusals and crashes
#: as well as the happy path.
Failure = Union[RejectPayload, ErrorPayload]


# ── Shared records ───────────────────────────────────────────────────────────

class TokenTotals(NovaOutput):
    """Token accounting for a session.

    ``models.UsageSummary`` was hand-flattened into this same three-key literal
    in three places (both session handlers and the usage resource).
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def from_usage(cls, usage: Any) -> "TokenTotals":
        """Build from a ``models.UsageSummary``."""
        return cls(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
        )


class StateVector(NovaOutput):
    """A shard's decoded epistemic state — see ``nova_shard_db.decode_state``."""

    confidence: float = Field(description="0.0-1.0, rounded to 4dp.")
    valence: int = Field(description="0 (most negative) to 9 (most positive).")
    arousal: int = Field(description="0 (calm) to 9 (activated).")
    epistemic: int = Field(description="0=contradicted, 1=neutral, 2=confirmed.")


class BrowseRow(NovaOutput):
    """One row from the compact browse projection (``store.build_browse_row``).

    The single-letter keys are deliberate — these rows are returned in bulk and
    the abbreviations meaningfully cut token cost.
    """

    id: str = Field(description="Shard id.")
    d: str = Field(description="One-line description, truncated to 80 chars.")
    t: list[str] = Field(default_factory=list, description="Tags.")
    c: float = Field(description="Confidence, 3dp.")
    created: str = Field(description="YYYY-MM-DD.")
    n: int = Field(description="Turn count.")
    s: Optional[str] = Field(
        default=None,
        description="Synopsis, up to 240 chars. Present only for nova_shard_summary.",
    )


class ThemeBucket(NovaOutput):
    """Rows grouped under one theme, when ``group_by_theme`` is set."""

    count: int
    shards: list[BrowseRow] = Field(default_factory=list)


class BrowseEnvelope(NovaOutput):
    """The ``_v: 3`` browse payload shared by the three browse tools.

    ``shards`` and ``themes`` are mutually exclusive: ``group_by_theme`` selects
    one or the other, never both.
    """

    v: int = Field(default=3, alias="_v", description="Envelope version.")
    tool: Optional[str] = Field(default=None, description="Which browse tool answered.")
    total: int = Field(description="Rows matching the filter, before pagination.")
    returned: int = Field(description="Rows in this page.")
    shards: Optional[list[BrowseRow]] = None
    themes: Optional[dict[str, ThemeBucket]] = None

    model_config = ConfigDict(extra="allow", populate_by_name=True)


# ── Session tools ────────────────────────────────────────────────────────────

class SessionFlushed(NovaOutput):
    """``nova_session_flush`` — the session was written to disk and dropped from
    memory."""

    status: Literal["flushed"] = "flushed"
    session_id: str
    message_count: int
    token_totals: TokenTotals


class SessionLoaded(NovaOutput):
    """``nova_session_load`` — a flushed session was restored into memory."""

    status: Literal["loaded"] = "loaded"
    session_id: str
    message_count: int
    created_at: str
    last_active: str
    token_totals: TokenTotals


class SessionList(NovaOutput):
    """``nova_session_list`` — session ids currently persisted on disk."""

    status: Literal["ok"] = "ok"
    sessions: list[str] = Field(default_factory=list)
    count: int = 0


SessionFlushResult = Union[SessionFlushed, RejectPayload, ErrorPayload]
SessionLoadResult = Union[SessionLoaded, RejectPayload, ErrorPayload]
SessionListResult = Union[SessionList, RejectPayload]
