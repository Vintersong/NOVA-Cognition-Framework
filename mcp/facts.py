"""
facts.py — `nova_facts_search` MCP tool registration.

Wires `shard_parser.ShardDB` (SQLite-backed `.shard` corpus with discrete
{-1, 0, 1} confidence) as a HUGINN pre-filter. Distinct from the float-
confidence JSON shard store: facts are curated, immutable-ish, and
queried by keyword before HUGINN's dense retrieval runs.

Tools:
    nova_facts_search(query, confidence=1, limit=10) — keyword search
    nova_facts_rebuild()                              — re-index facts/
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from config import FACTS_DIR, FACTS_INDEX_FILE
from permissions import is_blocked, denial_payload
from shard_parser import ShardDB
from tool_registry import nova_tool

logger = logging.getLogger(__name__)


class FactsSearchInput(BaseModel):
    query: str = Field(..., min_length=1)
    confidence: int | None = Field(
        default=1,
        description="Discrete confidence filter. 1=confirmed (default), 0=neutral, -1=contradicted, null=any.",
    )
    limit: int = Field(default=10, ge=1, le=50)


class FactsRebuildInput(BaseModel):
    pass


def _open_db() -> ShardDB:
    return ShardDB(FACTS_INDEX_FILE)


def search_facts(query: str, confidence: int | None = 1, limit: int = 10) -> list[dict[str, Any]]:
    """Library-callable form. Used by `nova_shard_interact` as a pre-filter."""
    if not Path(FACTS_INDEX_FILE).exists() and not Path(FACTS_DIR).is_dir():
        return []
    try:
        with _open_db() as db:
            return db.search(query, confidence=confidence, limit=limit)
    except Exception as exc:
        logger.warning("nova_facts_search failed: %s", exc)
        return []


def register_facts_tools(mcp: Any) -> None:
    """Register `nova_facts_search` and `nova_facts_rebuild` on the MCP server."""

    @nova_tool(mcp, name="nova_facts_search")
    async def nova_facts_search(params: FactsSearchInput) -> str:
        """Keyword search over the curated facts corpus (`.shard` files).
        Returns high-confidence facts as a HUGINN pre-filter."""
        if is_blocked("nova_facts_search"):
            return denial_payload("nova_facts_search")
        results = search_facts(params.query, confidence=params.confidence, limit=params.limit)
        return json.dumps({
            "status": "ok",
            "query": params.query,
            "confidence_filter": params.confidence,
            "count": len(results),
            "results": results,
        }, indent=2)

    @nova_tool(mcp, name="nova_facts_rebuild")
    async def nova_facts_rebuild(params: FactsRebuildInput) -> str:
        """Re-scan FACTS_DIR and rebuild the SQLite index. Idempotent."""
        if is_blocked("nova_facts_rebuild"):
            return denial_payload("nova_facts_rebuild")
        try:
            with _open_db() as db:
                count = db.rebuild_from_dir(FACTS_DIR)
            return json.dumps({"status": "ok", "indexed": count, "facts_dir": FACTS_DIR})
        except Exception as exc:
            logger.warning("nova_facts_rebuild failed: %s", exc)
            return json.dumps({"status": "error", "message": str(exc)})
