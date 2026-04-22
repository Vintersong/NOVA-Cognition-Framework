"""
schemas.py — Pydantic input models for all 30 NOVA MCP tools.

Extracted from nova_server.py so tool handlers remain a thin adapter layer.
"""

from typing import Literal, Optional
from pydantic import BaseModel, Field, ConfigDict

SESSION_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"


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
    relation_type: str = Field(default="references")


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
    relation_type: str = Field(..., min_length=1)
    notes: str = Field(default="")


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
