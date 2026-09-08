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
    "ThemeBucket",
    "BrowseEnvelope",
    "EpistemicProvenance",
    "ShardMetaTags",
    "ShardContext",
    "ConversationTurn",
    "Shard",
    "FactRow",
    "SessionFlushed",
    "SessionLoaded",
    "SessionList",
    "SessionFlushResult",
    "SessionLoadResult",
    "SessionListResult",
    "GraphRelation",
    "GraphHop",
    "GraphTransitiveResult",
    "GraphDirectResult",
    "RelationAdded",
    "GraphQueryResult",
    "GraphRelateResult",
    "LoadedShard",
    "InteractLoaded",
    "InteractNoShards",
    "ShardCreated",
    "ShardUpdated",
    "ShardValidated",
    "SearchHit",
    "SearchResults",
    "StateStats",
    "StateHit",
    "StateFilters",
    "StateQueryResults",
    "ObsidianDryRun",
    "ObsidianExported",
    "LegacyShardDump",
    "ShardBody",
    "ShardsMerged",
    "ShardArchived",
    "ShardForgotten",
    "ConsolidateScheduled",
    "ConsolidateNoReport",
    "ConsolidateLastReport",
    "ShardInteractResult",
    "ShardCreateResult",
    "ShardUpdateResult",
    "ShardValidateResult",
    "ShardSearchResult",
    "ShardStateResult",
    "ObsidianExportResult",
    "ShardBrowseResult",
    "ShardDumpResult",
    "ShardGetResult",
    "ShardGetFullResult",
    "ShardMergeResult",
    "ShardArchiveResult",
    "ShardForgetResult",
    "ShardConsolidateResult",
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
    """The ``_v: 3`` browse payload shared by ``nova_shard_index`` and
    ``nova_shard_summary``.

    ``shards`` and ``themes`` are mutually exclusive: ``group_by_theme`` selects
    one or the other, never both.
    """

    v: int = Field(default=3, alias="_v", description="Envelope version.")
    tool: Optional[str] = Field(default=None, description="Which browse tool answered.")
    total: int = Field(description="Rows matching the filter, before pagination.")
    page: int = 1
    per_page: int = 100
    returned: int = Field(description="Rows in this page.")
    sort: str = ""
    sort_order: str = ""
    shards: Optional[list[BrowseRow]] = None
    themes: Optional[dict[str, ThemeBucket]] = None

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class EpistemicProvenance(NovaOutput):
    """A shard's chain of custody — see ``provenance.py``."""

    source_type: str = Field(description="See provenance.AUTHORITY_RANK.")
    validator: Optional[str] = None
    mechanism: Optional[str] = None
    confidence_delta: float = 0.0
    validated_at: Optional[str] = None
    superseded: bool = False
    superseded_by: Optional[str] = None
    events: list[dict[str, Any]] = Field(default_factory=list)


class ShardMetaTags(NovaOutput):
    """A shard's ``meta_tags`` block.

    Every field is optional and loosely typed on purpose: this describes shards
    written by five years of importers and three shard formats, and a read tool
    must not fail validation on a legacy record it can still return. ``Any``
    appears where the on-disk type genuinely varies.
    """

    intent: Optional[str] = None
    theme: Optional[str] = None
    source: Optional[str] = None
    confidence: Optional[float] = None
    usage_count: Optional[int] = None
    last_used: Optional[str] = None
    enrichment_status: Optional[str] = None
    quarantine_until: Optional[str] = None
    valence: Optional[int] = None
    arousal: Optional[int] = None
    provenance: Optional[str] = Field(
        default=None, description='The legacy origin triple, e.g. "en|WEIRD|LLM".',
    )
    cluster_id: Any = None
    last_compacted: Optional[str] = None
    superseded_by: Optional[str] = None
    project_context: Optional[str] = None
    validity_window: Optional[dict[str, Any]] = None
    epistemic_provenance: Optional[EpistemicProvenance] = None


class ShardContext(NovaOutput):
    """A shard's ``context`` block. The embedding itself is not published."""

    summary: str = ""
    topics: list[str] = Field(default_factory=list)
    conversation_type: Optional[str] = None
    embedding_sig: Optional[str] = None


class ConversationTurn(NovaOutput):
    """One entry in ``conversation_history``."""

    timestamp: str = ""
    user: str = ""
    ai: str = ""


class Shard(NovaOutput):
    """A whole shard as it sits on disk.

    Both shard formats deserialise to this shape — ``shard_format.md_to_shard``
    reconstructs the same nested dict the ``.json`` format stores directly.
    """

    shard_id: str = ""
    guiding_question: str = ""
    conversation_history: list[ConversationTurn] = Field(default_factory=list)
    meta_tags: ShardMetaTags = Field(default_factory=ShardMetaTags)
    context: ShardContext = Field(default_factory=ShardContext)
    tags: list[str] = Field(default_factory=list)


class FactRow(NovaOutput):
    """One hit from the curated facts corpus (``shard_parser.ShardDB.search``).

    Note ``confidence`` here is the discrete {-1, 0, 1} of the facts corpus, not
    the float confidence of the JSON shard store.
    """

    id: str
    topic: str = ""
    tier: Optional[str] = None
    confidence: int = 0
    links: list[str] = Field(default_factory=list)
    timestamp: Optional[str] = None
    content: str = ""


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


# ── Graph tools ──────────────────────────────────────────────────────────────

class GraphRelation(NovaOutput):
    """One stored relation, enriched with both endpoints' guiding questions."""

    source: str
    target: str
    type: str = Field(description="See schemas.RelationType for the vocabulary.")
    notes: str = ""
    created_at: str = ""
    reason: Optional[str] = Field(
        default=None, description="Required for `supersedes`; absent otherwise.",
    )
    source_question: str = ""
    target_question: str = ""


class GraphHop(NovaOutput):
    """One node reached by the transitive walk."""

    shard_id: str
    depth: int
    path: list[str] = Field(default_factory=list)
    relation_type: str = ""


class GraphTransitiveResult(NovaOutput):
    """``nova_graph_query`` with ``transitive=True`` — a BFS from one root."""

    mode: Literal["transitive"] = "transitive"
    root: str
    direction: Literal["outbound", "inbound"]
    relation_type: str = Field(description='The filter, or "any".')
    max_depth: int
    results: list[GraphHop] = Field(default_factory=list)
    total_entities: int
    total_relations: int


class GraphDirectResult(NovaOutput):
    """``nova_graph_query`` without ``transitive`` — relations matching a pattern."""

    mode: Literal["direct"] = "direct"
    pattern: dict[str, str] = Field(
        default_factory=dict,
        description="The filter that was applied — any of source, target, type.",
    )
    relations: list[GraphRelation] = Field(default_factory=list)
    total_entities: int
    total_relations: int


class RelationAdded(NovaOutput):
    """``nova_graph_relate`` — a directed relation was written to the graph."""

    status: Literal["relation_added"] = "relation_added"
    source: str
    target: str
    type: str
    notes: str = ""
    confidence_after_corroboration: Optional[float] = Field(
        default=None,
        description=(
            "The source shard's new confidence. Non-null only for "
            "relation_type='corroborated_by', and only when the raise succeeded. "
            "This is the sanctioned path for raising a shard's confidence."
        ),
    )


GraphQueryResult = Union[GraphTransitiveResult, GraphDirectResult, RejectPayload]
GraphRelateResult = Union[RelationAdded, RejectPayload]


# ── Shard tools ──────────────────────────────────────────────────────────────

class LoadedShard(NovaOutput):
    """One shard as ``nova_shard_interact`` presents it — metadata plus the
    rendered tail of the conversation, not the raw record."""

    shard_id: str
    guiding_question: str = ""
    meta_tags: ShardMetaTags = Field(default_factory=ShardMetaTags)
    confidence: float = 1.0
    tags: list[str] = Field(default_factory=list)
    fragment_count: int = 0
    fragments: list[str] = Field(
        default_factory=list,
        description="`[SHARD: id] User:/NOVA:` lines, capped at MAX_FRAGMENTS turns.",
    )
    context_summary: str = ""


class InteractLoaded(NovaOutput):
    """``nova_shard_interact`` — shards were selected and loaded."""

    status: Literal["loaded"] = "loaded"
    inferred: bool = Field(
        description="True when HUGINN/MUNINN chose the shards rather than the caller.",
    )
    huginn_confidence: float = 0.0
    muninn_used: bool = False
    facts: list[FactRow] = Field(default_factory=list)
    shards: list[LoadedShard] = Field(default_factory=list)
    errors: list[str] = Field(
        default_factory=list,
        description="Requested shard ids that could not be loaded.",
    )


class InteractNoShards(NovaOutput):
    """``nova_shard_interact`` — nothing matched.

    Not a reject: asking a memory that holds nothing on the subject is a
    legitimate answer, and the caller's next step is to search or create.
    """

    status: Literal["no_shards_found"] = "no_shards_found"
    message: str
    suggestion: str


class ShardCreated(NovaOutput):
    """``nova_shard_create`` — the shard is on disk; enrichment runs after."""

    status: Literal["created"] = "created"
    shard_id: str
    guiding_question: str
    enrichment_status: str = Field(
        default="pending",
        description="Always 'pending' — embedding and topics land in the background.",
    )


class ShardUpdated(NovaOutput):
    """``nova_shard_update`` — a turn was appended."""

    status: Literal["updated"] = "updated"
    shard_id: str
    total_entries: int
    nott_scheduled: bool = True
    enrichment_status: str = "pending"


class ShardValidated(NovaOutput):
    """``nova_shard_validate`` — a validation event was recorded."""

    status: Literal["validated"] = "validated"
    shard_id: str
    confidence: Optional[float] = Field(
        default=None, description="The shard's confidence after the event.",
    )
    epistemic_provenance: Optional[EpistemicProvenance] = None


class SearchHit(NovaOutput):
    """One row from ``nova_shard_search``'s local keyword pass."""

    shard_id: str
    guiding_question: str = ""
    relevance_score: float = Field(description="Token overlap, before weighting.")
    confidence: float
    weighted_score: float = Field(description="What the ranking actually sorts on.")
    tags: list[str] = Field(default_factory=list)
    context_summary: str = ""


class SearchResults(NovaOutput):
    """``nova_shard_search``.

    ``results`` is the local keyword ranking; ``huginn_ranking`` is the raven
    pass's ordering of shard ids. They are deliberately separate — the raven
    ranking is advisory and may be empty when the API key is absent or the
    call timed out.
    """

    query: str
    results: list[SearchHit] = Field(default_factory=list)
    total_searched: int
    huginn_confidence: float = 0.0
    muninn_used: bool = False
    huginn_ranking: list[str] = Field(default_factory=list)


class StateStats(NovaOutput):
    """``nova_shard_query_state`` with ``stats_only`` — the state distribution."""

    stats: dict[str, int] = Field(default_factory=dict)


class StateHit(NovaOutput):
    """One shard matching a state-vector query."""

    shard_id: str
    guiding_question: str = ""
    confidence: float
    state: StateVector
    provenance: Optional[str] = None
    theme: Optional[str] = None
    quarantine_until: Optional[str] = None


class StateFilters(NovaOutput):
    """The filter that produced a ``StateQueryResults``, echoed back."""

    min_confidence: float
    max_confidence: float
    epistemic: Optional[int] = None
    valence_min: Optional[int] = None
    keyword: Optional[str] = None


class StateQueryResults(NovaOutput):
    """``nova_shard_query_state`` — shards matching the state vector."""

    returned: int
    filters: StateFilters
    results: list[StateHit] = Field(default_factory=list)


class ObsidianDryRun(NovaOutput):
    """``nova_obsidian_export`` with ``dry_run`` — counts only, nothing written."""

    dry_run: Literal[True] = True
    would_export: int
    would_skip: int
    out_dir: str


class ObsidianExported(NovaOutput):
    """``nova_obsidian_export`` — the vault was written."""

    exported: int
    skipped: int
    errors: list[str] = Field(
        default_factory=list, description="First 10 per-shard failures.",
    )
    out_dir: str
    index_note: str


class LegacyShardDump(NovaOutput):
    """``nova_shard_list`` — whole shards, one page at a time.

    Deprecated in favour of the browse tools, which return compact rows instead
    of full conversation bodies.
    """

    v: int = Field(default=3, alias="_v", description="Envelope version.")
    deprecated: Literal[True] = True
    mode: str
    message: str
    total: int
    offset: int
    limit: int
    returned: int
    shards: list[Shard] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class ShardBody(NovaOutput):
    """``nova_shard_get_full`` — the conversation body without the metadata."""

    shard_id: str
    guiding_question: str = ""
    source: str = "agent_inference"
    summary: str = ""
    body: list[ConversationTurn] = Field(default_factory=list)


class ShardsMerged(NovaOutput):
    """``nova_shard_merge`` — a meta-shard was created from several sources."""

    status: Literal["merged"] = "merged"
    new_shard_id: str
    sources: list[str] = Field(default_factory=list)
    total_entries: int
    originals_archived: bool


class ShardArchived(NovaOutput):
    """``nova_shard_archive`` — the shard is deprioritised, not deleted."""

    status: Literal["archived"] = "archived"
    shard_id: str
    guiding_question: str = ""


class ShardForgotten(NovaOutput):
    """``nova_shard_forget`` — the shard is excluded from recall but kept on
    disk for audit."""

    status: Literal["forgotten"] = "forgotten"
    shard_id: str
    reason: str
    note: str


class ConsolidateScheduled(NovaOutput):
    """``nova_shard_consolidate`` — a NÓTT cycle was started in the background."""

    status: Literal["scheduled"] = "scheduled"
    message: str


class ConsolidateNoReport(NovaOutput):
    """``nova_shard_consolidate`` with ``dry_run`` before any cycle has run."""

    status: Literal["no_report_yet"] = "no_report_yet"
    hint: str


class ConsolidateLastReport(NovaOutput):
    """``nova_shard_consolidate`` with ``dry_run`` — the last cycle's report.

    The body is whatever ``nott.NottReport.to_dict`` produced, or the skipped /
    error stub the worker thread left behind, so the shape is open. ``status``
    is the report's own — a dry run reports what happened, and overwriting it
    with a literal would hide an errored cycle behind an "ok"-looking envelope.
    """

    status: str


ShardInteractResult = Union[InteractLoaded, InteractNoShards, RejectPayload]
ShardCreateResult = Union[ShardCreated, RejectPayload]
ShardUpdateResult = Union[ShardUpdated, RejectPayload]
ShardValidateResult = Union[ShardValidated, RejectPayload]
ShardSearchResult = Union[SearchResults, RejectPayload]
ShardStateResult = Union[StateStats, StateQueryResults, RejectPayload]
ObsidianExportResult = Union[ObsidianDryRun, ObsidianExported, RejectPayload]
ShardBrowseResult = Union[BrowseEnvelope, RejectPayload]
ShardDumpResult = Union[LegacyShardDump, RejectPayload]
ShardGetResult = Union[Shard, RejectPayload]
ShardGetFullResult = Union[ShardBody, RejectPayload]
ShardMergeResult = Union[ShardsMerged, RejectPayload]
ShardArchiveResult = Union[ShardArchived, RejectPayload]
ShardForgetResult = Union[ShardForgotten, RejectPayload]
ShardConsolidateResult = Union[
    ConsolidateScheduled, ConsolidateLastReport, ConsolidateNoReport, RejectPayload,
]


# ── Wiki tools ───────────────────────────────────────────────────────────────

class WikiSpec(NovaOutput):
    """One entry in the wiki taxonomy — ``wiki.WikiPageSpec``."""

    slug: str
    title: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    category: str = "general"


class WikiSchema(NovaOutput):
    """``nova_wiki_schema`` with ``action="get"`` — the whole taxonomy."""

    page_count: int
    pages: list[WikiSpec] = Field(default_factory=list)


class WikiSpecAdded(NovaOutput):
    """``nova_wiki_schema`` with ``action="add"``."""

    status: Literal["added"] = "added"
    slug: str
    title: str
    total: int = Field(description="Specs in the taxonomy after the add.")


class WikiSpecRemoved(NovaOutput):
    """``nova_wiki_schema`` with ``action="remove"``."""

    status: Literal["removed"] = "removed"
    slug: str
    total: int
    note: str


class WikiIngested(NovaOutput):
    """``nova_wiki_ingest`` — the routing pass ran, and the synthesis pass too
    unless ``dry_run``."""

    source_name: str
    routed_slugs: list[str] = Field(default_factory=list)
    synthesized: list[str] = Field(default_factory=list)
    skipped: list[dict[str, str]] = Field(default_factory=list)
    dry_run: bool = False
    message: Optional[str] = None


class WikiIngestNoSchema(NovaOutput):
    """``nova_wiki_ingest`` with an empty taxonomy — there is nowhere to route to.

    A distinct shape rather than a field on ``WikiIngested``: this branch reports
    a ``status`` and carries no ``source_name`` or ``skipped``, and pretending
    otherwise would describe a payload the tool never sends.
    """

    status: Literal["no_schema"] = "no_schema"
    message: str
    routed_slugs: list[str] = Field(default_factory=list)
    synthesized: list[str] = Field(default_factory=list)
    dry_run: bool = False


class WikiQueryHit(NovaOutput):
    """One ``nova_wiki_query`` result."""

    slug: str
    title: str
    score: Union[int, float] = Field(
        description=(
            "A cosine similarity (float) on the embedding path, or a raw "
            "occurrence count (int) on the keyword fallback. `method` says which."
        ),
    )
    excerpt: str = ""
    method: Literal["cosine", "keyword"]


class WikiQueryResults(NovaOutput):
    """``nova_wiki_query``."""

    query: str
    results: list[WikiQueryHit] = Field(default_factory=list)


class WikiPageContent(NovaOutput):
    """``nova_wiki_get`` — one page in full."""

    slug: str
    title: str
    category: str = "general"
    tags: list[str] = Field(default_factory=list)
    updated: str = Field(description="ISO 8601. nova_wiki_list uses %Y-%m-%d instead.")
    sources: list[str] = Field(
        default_factory=list,
        description="Names of the ingested documents. nova_wiki_list reports a count.",
    )
    links: list[str] = Field(default_factory=list, description="Outbound [[wikilinks]].")
    body: str = ""


class WikiListRow(NovaOutput):
    """One row from ``nova_wiki_list``.

    ``sources`` and ``updated`` deliberately differ from ``WikiPageContent``:
    this is a listing projection, so a count and a date beat a name list and a
    timestamp. They are separate models precisely so the schema can say that.
    """

    slug: str
    title: str
    category: str = "general"
    tags: list[str] = Field(default_factory=list)
    updated: str = Field(description="%Y-%m-%d.")
    sources: int = Field(description="How many documents fed this page.")
    summary: str = Field(description="First prose line, capped at 120 chars.")


class WikiList(NovaOutput):
    """``nova_wiki_list``."""

    total: int
    category: str = Field(description='The filter, or "all".')
    pages: list[WikiListRow] = Field(default_factory=list)


class WikiBrokenLink(NovaOutput):
    """A `[[wikilink]]` pointing at a slug with no page."""

    page: str
    broken_link: str


class WikiStalePage(NovaOutput):
    """A page untouched for more than 30 days."""

    slug: str
    days_since_update: int


class WikiLintReport(NovaOutput):
    """``nova_wiki_lint``."""

    total_pages: int
    orphan_pages: list[str] = Field(
        default_factory=list, description="No inbound [[wikilinks]].",
    )
    broken_links: list[WikiBrokenLink] = Field(default_factory=list)
    missing_embeddings: list[str] = Field(default_factory=list)
    stale_pages: list[WikiStalePage] = Field(default_factory=list)
    deep_lint: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "Null unless deep=True. Then {slug: [contradiction, ...]}, or a "
            "reject/error envelope if the LLM pass could not run."
        ),
    )


WikiSchemaResult = Union[WikiSchema, WikiSpecAdded, WikiSpecRemoved, RejectPayload]
WikiIngestResult = Union[WikiIngested, WikiIngestNoSchema, RejectPayload]
WikiQueryResult = Union[WikiQueryResults, RejectPayload]
WikiGetResult = Union[WikiPageContent, RejectPayload]
WikiListResult = Union[WikiList, RejectPayload]
WikiLintResult = Union[WikiLintReport, RejectPayload]


# ── Facts, code search, HUGINN pre-filter, calibration ───────────────────────

class FactsSearchResults(NovaOutput):
    """``nova_facts_search``."""

    status: Literal["ok"] = "ok"
    query: str
    confidence_filter: Optional[int] = Field(
        default=None, description="1=confirmed, 0=neutral, -1=contradicted, null=any.",
    )
    count: int
    results: list[FactRow] = Field(default_factory=list)


class FactsRebuilt(NovaOutput):
    """``nova_facts_rebuild``."""

    status: Literal["ok"] = "ok"
    indexed: int = Field(description="Files re-indexed from FACTS_DIR.")
    facts_dir: str


class CodeMatch(NovaOutput):
    """One chunk from ``nova_code_search``."""

    file: str
    symbol: str = ""
    kind: str = Field(
        description="function | class | module_header | function_header | class_header",
    )
    start_line: int
    end_line: int
    similarity_score: float
    source: str = Field(description="The chunk's source text, verbatim.")


class CodeSearchResults(NovaOutput):
    """``nova_code_search``."""

    query: str
    match_count: int
    matches: list[CodeMatch] = Field(default_factory=list)


class CodeSearchUnavailable(NovaOutput):
    """``nova_code_search`` with no embedding model — nothing to search with."""

    status: Literal["unavailable"] = "unavailable"
    reason: str


class HuginnCandidate(NovaOutput):
    """One shard surviving the HUGINN pre-filter."""

    id: str
    guiding_question: str = ""
    context_summary: str = Field(default="", description="Capped at 300 chars.")
    confidence: float


class HuginnCandidates(NovaOutput):
    """``nova_huginn_candidates``."""

    query: str
    candidate_count: int
    candidates: list[HuginnCandidate] = Field(default_factory=list)
    huginn_prompt_block: str = Field(
        description=(
            "The candidates rendered for pasting into a HUGINN agent prompt. "
            "Its exact bytes are the point, so it stays a string rather than "
            "being re-derived from `candidates` by the caller."
        ),
    )


class HuginnBucketStat(NovaOutput):
    """Top-1 consistency within one confidence bucket."""

    total: int
    consistent: int
    consistency_rate: float


class HuginnCalibration(NovaOutput):
    """The HUGINN half of ``nova_calibrate_routing``."""

    current_threshold: float
    suggested_threshold: float
    threshold_delta: float
    sample_size: int
    bucket_stats: dict[str, HuginnBucketStat] = Field(default_factory=dict)
    note: str = ""


class ReplayDivergence(NovaOutput):
    """Repeated-query divergence — the same query returning different top-1
    shards across runs."""

    queries_repeated: int
    queries_zero_hit: int = Field(
        description="Repeated queries whose every run missed; excluded from the rate.",
    )
    queries_divergent: int
    divergence_rate: float
    examples: list[Any] = Field(default_factory=list)
    note: str = ""


class ForgemasterCalibration(NovaOutput):
    """The Forgemaster half of ``nova_calibrate_routing``."""

    total_outcome_events: int
    routing_stats: dict[str, Any] = Field(default_factory=dict)
    note: str = ""


class CalibrationReport(NovaOutput):
    """``nova_calibrate_routing`` — read-only findings; nothing is applied."""

    huginn: HuginnCalibration
    replay_divergence: Optional[ReplayDivergence] = Field(
        default=None, description="Present only when include_replay_divergence.",
    )
    forgemaster: Optional[ForgemasterCalibration] = Field(
        default=None, description="Present only when include_forgemaster.",
    )


FactsSearchResult = Union[FactsSearchResults, RejectPayload]
FactsRebuildResult = Union[FactsRebuilt, RejectPayload, ErrorPayload]
CodeSearchResult = Union[CodeSearchResults, CodeSearchUnavailable, RejectPayload]
HuginnCandidatesResult = Union[HuginnCandidates, RejectPayload]
CalibrateRoutingResult = Union[CalibrationReport, RejectPayload]
