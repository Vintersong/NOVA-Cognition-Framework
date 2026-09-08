"""
graph_tools.py — knowledge-graph query and mutation tools.

Two handlers: ``nova_graph_query`` (read) and ``nova_graph_relate`` (write,
gated). Registered via ``register_graph_tools(mcp, ctx)``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from gate_helpers import gate_check_model, log_executed, permission_reject
from graph import (
    add_relation,
    load_graph,
    query_graph,
    query_graph_transitive,
)
from maintenance import apply_confidence_corroboration
from outputs import (
    GraphDirectResult,
    GraphHop,
    GraphQueryResult,
    GraphRelateResult,
    GraphRelation,
    GraphTransitiveResult,
    RelationAdded,
)
from reject import RejectCode, reject_model
from schemas import GraphQueryInput, GraphRelationInput
from store import load_shard, patch_index_entry, save_shard
from tool_registry import nova_tool

if TYPE_CHECKING:
    from server_context import ServerContext


def register_graph_tools(mcp, ctx: "ServerContext") -> None:

    @nova_tool(mcp, name="nova_graph_query")
    async def nova_graph_query(params: GraphQueryInput) -> GraphQueryResult:
        """
        Query the inter-shard knowledge graph.
        Find what a shard influences, depends on, extends, contradicts, or references.
        All parameters optional — omit to return all relations.
        Set transitive=True to traverse the graph by BFS up to max_depth hops.

        Relation types: influences, depends_on, contradicts, extends, references, merged_from, supersedes, corroborated_by
        """
        if ctx.permission_context.blocks("nova_graph_query"):
            return permission_reject("nova_graph_query")
        loop = asyncio.get_running_loop()
        if params.transitive:
            root_id = params.source or params.target
            if not root_id:
                return reject_model(
                    RejectCode.INVALID_INPUT,
                    "Transitive query requires 'source' or 'target'.",
                )
            direction = "outbound" if params.source else "inbound"
            results = query_graph_transitive(
                root_id=root_id,
                relation_type=params.relation_type or None,
                direction=direction,
                max_depth=params.max_depth,
            )
            graph = await loop.run_in_executor(None, load_graph)
            return GraphTransitiveResult(
                root=root_id,
                direction=direction,
                relation_type=params.relation_type or "any",
                max_depth=params.max_depth,
                results=[GraphHop(**hop) for hop in results],
                total_entities=len(graph.get("entities", {})),
                total_relations=len(graph.get("relations", [])),
            )

        pattern = {}
        if params.source:
            pattern["source"] = params.source
        if params.target:
            pattern["target"] = params.target
        if params.relation_type:
            pattern["type"] = params.relation_type

        relations = query_graph(pattern)
        graph = await loop.run_in_executor(None, load_graph)

        enriched = []
        for r in relations:
            source_entity = graph.get("entities", {}).get(r["source"], {})
            target_entity = graph.get("entities", {}).get(r["target"], {})
            enriched.append(GraphRelation(
                **r,
                source_question=source_entity.get("guiding_question", ""),
                target_question=target_entity.get("guiding_question", ""),
            ))

        return GraphDirectResult(
            pattern=pattern,
            relations=enriched,
            total_entities=len(graph.get("entities", {})),
            total_relations=len(graph.get("relations", [])),
        )

    @nova_tool(mcp, name="nova_graph_relate")
    async def nova_graph_relate(params: GraphRelationInput) -> GraphRelateResult:
        """
        Manually add a directed relation between two shards in the knowledge graph.
        Use this when you notice a connection that wasn't auto-detected.

        Relation types:
          influences       — shard A shapes the thinking in shard B
          depends_on       — shard A requires shard B to make sense
          contradicts      — shards are in tension, revisit both
          extends          — shard A builds on shard B
          references       — shard A cites or mentions shard B
          supersedes       — shard A replaces shard B (reason field required)
          corroborated_by  — shard A is confirmed by shard B
        """
        if ctx.permission_context.blocks("nova_graph_relate"):
            return permission_reject("nova_graph_relate")
        gate_err, request_id = await gate_check_model(
            ctx, "nova_graph_relate", params.source_id,
        )
        if gate_err:
            return gate_err
        op_ok = False
        try:
            add_relation(params.source_id, params.target_id, params.relation_type, params.notes, params.reason)

            confidence_bumped = None
            loop = asyncio.get_running_loop()
            if params.relation_type == "corroborated_by":
                try:
                    data, filepath = await loop.run_in_executor(None, load_shard, params.source_id)
                    new_conf = apply_confidence_corroboration(data)
                    await loop.run_in_executor(None, save_shard, filepath, data)
                    await loop.run_in_executor(None, patch_index_entry, params.source_id, data)
                    confidence_bumped = round(new_conf, 4)
                except Exception:
                    pass

            op_ok = True
            return RelationAdded(
                source=params.source_id,
                target=params.target_id,
                type=params.relation_type,
                notes=params.notes,
                confidence_after_corroboration=confidence_bumped,
            )
        finally:
            log_executed(ctx, request_id, "nova_graph_relate", params.source_id, op_ok)
