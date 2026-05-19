"""
schemas.py — Pydantic input models for all 37 NOVA MCP tools.

Extracted from nova_server.py so tool handlers remain a thin adapter layer.
"""

from typing import Literal, Optional
from pydantic import BaseModel, Field, ConfigDict, model_validator

from config import SESSION_ID_PATTERN


# Shared relation enum: same set used by ShardCreateInput.relation_type and
# GraphRelationInput.relation_type so creation-time wiring and explicit
# graph writes can't drift apart.
RelationType = Literal[
    "influences", "depends_on", "contradicts", "extends", "references",
    "merged_from", "supersedes", "corroborated_by",
]


# ── Shard tools ───────────────────────────────────────────────────────────────

class ShardListInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    mode: Literal['full'] = Field(default='full')
    limit: int = Field(default=50, ge=1, le=445)
    offset: int = Field(default=0, ge=0)
    tag_filter: str = Field(default="")


class ShardIndexInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    filter_tag: str = Field(default="")
    min_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    sort: Literal['confidence', 'created', 'turn_count', 'id'] = Field(default='confidence')
    sort_order: Literal['asc', 'desc'] = Field(default='desc')
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=100, ge=1, le=200)
    group_by_theme: bool = Field(default=False)


class ShardInteractInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    shard_ids: str = Field(default="")
    message: str = Field(..., min_length=1)
    auto_select: bool = Field(default=True)
    session_id: Optional[str] = Field(default=None, pattern=SESSION_ID_PATTERN)


class ShardCreateInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    guiding_question: str = Field(..., min_length=1)
    intent: str = Field(default="reflection")
    theme: str = Field(default="general")
    initial_message: str = Field(default="")
    related_shards: str = Field(default="")
    relation_type: RelationType = Field(default="references")
    reason: str = Field(
        default="",
        description="Required when relation_type='supersedes' — explain why the new shard supersedes the related ones.",
    )
    source: Literal[
        "user_input", "external_doc", "agent_inference", "session_extracted", "corroborated_by"
    ] = Field(default="agent_inference", description="Provenance of this shard")
    project_context: Optional[str] = Field(
        default=None,
        description="Project or context tag this shard applies to. Retrieval excludes it when NOVA_PROJECT_CONTEXT is set and doesn't match.",
    )
    validity_start: Optional[str] = Field(
        default=None,
        description="ISO 8601 timestamp — shard is excluded from retrieval before this date.",
    )
    validity_end: Optional[str] = Field(
        default=None,
        description="ISO 8601 timestamp — shard is excluded from retrieval after this date.",
    )

    @model_validator(mode="after")
    def _require_reason_for_supersedes(self) -> "ShardCreateInput":
        if self.relation_type == "supersedes" and self.related_shards.strip() and not self.reason:
            raise ValueError(
                "'reason' is required when relation_type is 'supersedes' and related_shards is set"
            )
        return self


class ShardUpdateInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    shard_id: str = Field(..., min_length=1)
    user_message: str = Field(default="")
    ai_response: str = Field(default="")


class ShardSearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    query: str = Field(..., min_length=1)
    top_n: int = Field(default=5, ge=1, le=20)
    include_low_confidence: bool = Field(default=False)


class ShardMergeInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    shard_ids: str = Field(..., min_length=1)
    new_guiding_question: str = Field(..., min_length=1)
    new_theme: str = Field(..., min_length=1)
    archive_originals: bool = Field(default=False)


class ShardArchiveInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    shard_id: str = Field(..., min_length=1)


class ShardForgetInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    shard_id: str = Field(..., min_length=1)
    reason: str = Field(default="")


class ShardGetInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    shard_id: str = Field(..., min_length=1)


class ShardConsolidateInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    dry_run: bool = Field(default=False)


class ShardGetFullInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    shard_id: str = Field(..., min_length=1)


# ── Obsidian export tool ──────────────────────────────────────────────────────

class ObsidianExportInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    out_dir: str = Field(default="", description="Override output directory (default: NOVA_OBSIDIAN_DIR or output/obsidian_vault/)")
    dry_run: bool = Field(default=False, description="Count what would be exported without writing files")


# ── State query tool ─────────────────────────────────────────────────────────

class ShardStateQueryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    max_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    epistemic: Optional[int] = Field(
        default=None,
        description="Filter by epistemic state: 0=contradicted, 1=neutral, 2=confirmed",
        ge=0, le=2,
    )
    valence_min: Optional[int] = Field(
        default=None,
        description="Minimum valence (0-9). 0=most negative, 9=most positive.",
        ge=0, le=9,
    )
    limit: int = Field(default=20, ge=1, le=100)
    keyword: str = Field(default="", description="Optional keyword filter on guiding_question/theme/intent")
    stats_only: bool = Field(default=False, description="Return state distribution stats instead of rows")


# ── Graph tools ───────────────────────────────────────────────────────────────

class GraphQueryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    source: str = Field(default="")
    target: str = Field(default="")
    relation_type: str = Field(default="")
    transitive: bool = Field(default=False)
    max_depth: int = Field(default=3, ge=1, le=10)


class GraphRelationInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    source_id: str = Field(..., min_length=1)
    target_id: str = Field(..., min_length=1)
    relation_type: RelationType = Field(..., description="Edge type")
    notes: str = Field(default="")
    reason: str = Field(default="", description="Required for supersedes edges — explain why source supersedes target")

    @model_validator(mode="after")
    def _require_reason_for_supersedes(self) -> "GraphRelationInput":
        if self.relation_type == "supersedes" and not self.reason:
            raise ValueError("'reason' is required when relation_type is 'supersedes'")
        return self


# ── Session tools ─────────────────────────────────────────────────────────────

class SessionFlushInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    session_id: str = Field(..., min_length=1, pattern=SESSION_ID_PATTERN)


class SessionLoadInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    session_id: str = Field(..., min_length=1, pattern=SESSION_ID_PATTERN)


class SessionListInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')


# ── Forgemaster tools ─────────────────────────────────────────────────────────

class ForgemasterSprintInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    sprint_id: str = Field(..., min_length=1)
    design_doc: str = Field(..., min_length=1)
    shard_ids: Optional[str] = Field(default=None)
    cached_system: str = Field(default="", description="System prompt from nova_cache_prewarm — passed to every Anthropic turn for cache reads")
    task_type: str = Field(default="", description="Task type hint for model routing (e.g. 'implementation', 'research', 'architecture'). Drives route_ticket() and is logged for empirical calibration.")


class CachePrewarmInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    top_n: int = Field(default=20, ge=5, le=100, description="Number of top-confidence shards to include")
    project_context: Optional[str] = Field(default=None, description="Filter shards by project_context tag")
    min_confidence: float = Field(default=0.6, ge=0.0, le=1.0, description="Minimum shard confidence floor")
    model: str = Field(default="", description="Model to prewarm against (defaults to MUNINN_MODEL)")


# ── Wiki tools ────────────────────────────────────────────────────────────────

class WikiSchemaInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    action: Literal["get", "add", "remove"] = Field(default="get")
    # For action="add": provide slug, title, description, tags (comma-sep), category
    slug: str = Field(default="")
    title: str = Field(default="")
    description: str = Field(default="")
    tags: str = Field(default="")
    category: str = Field(default="general")


class WikiIngestInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    # Local file path or raw text content
    source: str = Field(..., min_length=1, description="File path or raw text to ingest")
    source_name: str = Field(default="", description="Display name (auto-derived from path if blank)")
    dry_run: bool = Field(default=False)


class WikiQueryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    query: str = Field(..., min_length=1)
    top_n: int = Field(default=5, ge=1, le=20)


class WikiGetInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    slug: str = Field(..., min_length=1)


class WikiListInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    category: str = Field(default="")


class WikiLintInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    deep: bool = Field(default=False, description="Run LLM contradiction check across pages")


# ── Calibrate tools ───────────────────────────────────────────────────────────

class CalibrateRoutingInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    sample_size: int = Field(
        default=100, ge=10, le=500,
        description="Number of recent HUGINN log entries to sample for consistency analysis.",
    )
    k_runs: int = Field(
        default=5, ge=2, le=10,
        description="Minimum repeated-query occurrences required to count as consistent.",
    )
    include_forgemaster: bool = Field(
        default=True,
        description="Also parse FORGEMASTER_EVENT_LOG for routing success rates per model.",
    )
