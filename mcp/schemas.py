"""
schemas.py — Pydantic input models for all 16 NOVA MCP tools.

Extracted from nova_server.py so tool handlers remain a thin adapter layer.
"""

from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


# ── Shard tools ───────────────────────────────────────────────────────────────

class ShardInteractInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    shard_ids: str = Field(default="")
    message: str = Field(..., min_length=1)
    auto_select: bool = Field(default=True)
    session_id: Optional[str] = Field(default=None)


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
    session_id: str = Field(..., min_length=1)


class SessionLoadInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    session_id: str = Field(..., min_length=1)


class SessionListInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')


# ── Forgemaster tools ─────────────────────────────────────────────────────────

class ForgemasterSprintInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    sprint_id: str = Field(..., min_length=1)
    design_doc: str = Field(..., min_length=1)
    shard_ids: Optional[str] = Field(default=None)
