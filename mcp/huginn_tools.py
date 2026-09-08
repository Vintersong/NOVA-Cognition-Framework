"""
huginn_tools.py — MCP tool: nova_huginn_candidates

Registers the nova_huginn_candidates tool, which runs the orchestrator
pre-filter pipeline and returns a JSON candidate list ready to paste into
a HUGINN agent prompt. No LLM involved — pure keyword + confidence filtering.
"""

from __future__ import annotations

import json

from gate_helpers import permission_reject
from huginn_prefilter import prefilter
from outputs import HuginnCandidates, HuginnCandidatesResult
from schemas import HuginnCandidatesInput
from store import load_index, update_index
from tool_registry import nova_tool


def register_huginn_tools(mcp, ctx) -> None:

    @nova_tool(mcp, name="nova_huginn_candidates")
    async def nova_huginn_candidates(params: HuginnCandidatesInput) -> HuginnCandidatesResult:
        """
        Pre-filter the shard index for a query and return a small candidate list
        ready to pass to a HUGINN agent prompt.

        Runs keyword tokenization + confidence sort over the full index without
        any LLM call. Use this before spawning a HUGINN subagent so the agent
        receives only relevant candidates (typically 5-20) instead of the full
        1200+ shard corpus.

        Returns JSON:
          {
            "query": "<query>",
            "candidate_count": <int>,
            "candidates": [
              {"id": "...", "guiding_question": "...", "context_summary": "...", "confidence": 0.XX},
              ...
            ],
            "huginn_prompt_block": "<paste this into the HUGINN agent prompt>"
          }
        """
        if ctx.permission_context.blocks("nova_huginn_candidates"):
            return permission_reject("nova_huginn_candidates")

        query = params.query.strip()
        max_candidates = min(params.max_candidates, 20)

        import asyncio
        loop = asyncio.get_running_loop()
        index = await loop.run_in_executor(None, lambda: load_index() or update_index())
        candidates = prefilter(query, index, max_candidates=max_candidates)

        candidates_json = json.dumps(candidates, indent=2)
        huginn_prompt_block = (
            f"QUERY: {query}\n"
            f"CANDIDATES:\n{candidates_json}"
        )

        return HuginnCandidates(
            query=query,
            candidate_count=len(candidates),
            candidates=candidates,
            huginn_prompt_block=huginn_prompt_block,
        )
