"""
Schema validation tests for the shared RelationType alias.

Verifies that `ShardCreateInput.relation_type` and `GraphRelationInput.relation_type`
both reject unknown relation strings — closing the gap where shard creation
silently accepted any string and forwarded it to `add_relation()`.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas import ShardCreateInput, GraphRelationInput, RelationType


VALID_RELATIONS = (
    "influences", "depends_on", "contradicts", "extends",
    "references", "merged_from", "supersedes", "corroborated_by",
)


@pytest.mark.parametrize("relation", VALID_RELATIONS)
def test_shard_create_accepts_valid_relations(relation):
    if relation == "supersedes":
        # supersedes also requires reason at the GraphRelationInput layer;
        # ShardCreateInput just records the relation type to forward.
        pass
    ShardCreateInput(guiding_question="q", relation_type=relation)


def test_shard_create_rejects_unknown_relation():
    with pytest.raises(ValidationError):
        ShardCreateInput(guiding_question="q", relation_type="not_a_real_relation")


def test_graph_relation_rejects_unknown_relation():
    with pytest.raises(ValidationError):
        GraphRelationInput(source_id="a", target_id="b", relation_type="bogus")


def test_graph_relation_supersedes_requires_reason():
    with pytest.raises(ValidationError):
        GraphRelationInput(source_id="a", target_id="b", relation_type="supersedes")
    # With reason, it should accept.
    GraphRelationInput(
        source_id="a", target_id="b", relation_type="supersedes",
        reason="A replaces B because the underlying fact changed",
    )


def test_relation_type_alias_matches_both_models():
    """The shared alias is the single source of truth."""
    import typing
    args = set(typing.get_args(RelationType))
    assert args == set(VALID_RELATIONS)


def test_shard_create_supersedes_requires_reason_when_related_set():
    """Mirrors GraphRelationInput's supersedes validator so create-time
    supersedes can't bypass the reason requirement."""
    with pytest.raises(ValidationError):
        ShardCreateInput(
            guiding_question="q",
            relation_type="supersedes",
            related_shards="other-shard-id",
        )
    # With reason — accepted.
    ShardCreateInput(
        guiding_question="q",
        relation_type="supersedes",
        related_shards="other-shard-id",
        reason="The new shard subsumes the old one because the fact changed.",
    )


def test_shard_create_supersedes_without_related_is_allowed():
    """If no related_shards is given, supersedes is informational only —
    no graph write happens, so the reason check should not block creation."""
    ShardCreateInput(guiding_question="q", relation_type="supersedes")
