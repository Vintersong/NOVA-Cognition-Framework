"""
Capability gate policy, approval plumbing, and the audit trail.

This subsystem had no tests. Two behaviours in particular need pinning:

  * Which tools require human approval. The set is derived from
    `tool_registry.DESTRUCTIVE_CAPABILITIES` so it cannot drift from the
    `destructiveHint` published to clients — if those two disagree, a client is
    told a tool is safe while the gate treats it as dangerous, or the reverse.

  * The audit ordering. `audit_log.run_biconditional_check` reads an
    `irreversible.request` with no matching decision as a gate bypass, so the
    decision must be recorded for refusals too, and before the raise.
"""

from __future__ import annotations

import asyncio

import pytest

import tool_registry
from capability_gate import CapabilityDenied, CapabilityGate, HITLDenied
from skill_manifest import SkillManifest, VerificationLevel


class _RecordingAudit:
    """Captures the audit calls the gate makes, in order."""

    def __init__(self):
        self.events: list[tuple[str, str, object]] = []

    def log_denied(self, *, tool_name, reason, **kw):
        self.events.append(("denied", tool_name, reason))

    def log_request(self, *, tool_name, **kw):
        self.events.append(("request", tool_name, None))

    def log_decision(self, *, tool_name, approved, **kw):
        self.events.append(("decision", tool_name, approved))

    def kinds(self):
        return [e[0] for e in self.events]


class _FakeBroker:
    """Stands in for the terminal prompt; records that it was consulted."""

    def __init__(self, answer: bool):
        self.answer = answer
        self.calls: list[dict] = []

    def request(self, **kwargs) -> bool:
        self.calls.append(kwargs)
        return self.answer


def _gate(audit=None, broker=None) -> CapabilityGate:
    gate = CapabilityGate(audit_log=audit)
    if broker is not None:
        gate._broker = broker
    return gate


OPERATOR = SkillManifest.OPERATOR_DIRECT


# ── which tools need approval ────────────────────────────────────────────────

def test_approval_set_equals_the_destructive_hint_set():
    """The single most important invariant: what a client is told is dangerous
    is exactly what the operator gets asked about."""
    hinted = {
        name for name in tool_registry.all_names()
        if tool_registry.annotations_for(name)["destructiveHint"]
    }
    assert tool_registry.destructive_tools() == hinted


def test_expected_tools_require_approval():
    assert tool_registry.destructive_tools() == {
        "nova_shard_archive", "nova_shard_forget", "nova_shard_consolidate",
        "nidhogg_ingest", "nidhogg_scan", "nova_evolve", "nova_forgemaster_sprint",
    }


def test_graph_relate_does_not_require_approval():
    """Its `irreversible` flag exists to mint an audit request_id, not to claim
    it destroys anything — it fires on ordinary corroboration."""
    assert tool_registry.get("nova_graph_relate").irreversible is True
    assert tool_registry.requires_approval("nova_graph_relate") is False


# ── policy branches ──────────────────────────────────────────────────────────

def test_read_tool_proceeds_unaudited():
    audit = _RecordingAudit()
    assert _gate(audit).check("nova_shard_get", OPERATOR, "s1") is None
    assert audit.events == []


def test_reversible_irreversible_tool_is_approved_by_manifest():
    """nova_graph_relate is gated for audit but must not prompt."""
    audit = _RecordingAudit()
    broker = _FakeBroker(answer=False)
    request_id = _gate(audit, broker).check("nova_graph_relate", OPERATOR, "s1", "shard_a")
    assert request_id is not None
    assert audit.kinds() == ["request"]
    assert broker.calls == []


def test_destructive_tool_consults_the_broker_even_when_tested():
    """OPERATOR_DIRECT is TESTED with a wildcard capability, so without the
    destructive rule nothing could ever reach a broker."""
    assert OPERATOR.verification is VerificationLevel.TESTED
    broker = _FakeBroker(answer=True)
    _gate(_RecordingAudit(), broker).check("nova_shard_forget", OPERATOR, "s1", "shard_a")
    assert len(broker.calls) == 1
    assert broker.calls[0]["tool_name"] == "nova_shard_forget"
    assert broker.calls[0]["target"] == "shard_a"


def test_broker_refusal_raises():
    with pytest.raises(HITLDenied):
        _gate(_RecordingAudit(), _FakeBroker(answer=False)).check(
            "nova_shard_forget", OPERATOR, "s1", "shard_a"
        )


def test_undeclared_capability_is_hard_denied():
    audit = _RecordingAudit()
    empty = SkillManifest(skill_id="empty", verification=VerificationLevel.TESTED,
                          capabilities=frozenset())
    with pytest.raises(CapabilityDenied):
        _gate(audit).check("nova_shard_forget", empty, "s1")
    assert audit.kinds() == ["denied"]


def test_unknown_tool_is_denied_not_defaulted():
    with pytest.raises(CapabilityDenied):
        _gate().check("nova_not_a_real_tool", OPERATOR, "s1")


# ── audit ordering ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("approved", [True, False])
def test_decision_is_recorded_for_both_outcomes(approved):
    """A request with no decision reads as a gate bypass in the biconditional
    check, so the decision must be written even when refusing."""
    audit = _RecordingAudit()
    gate = _gate(audit, _FakeBroker(answer=approved))
    try:
        gate.check("nova_shard_forget", OPERATOR, "s1", "shard_a")
    except HITLDenied:
        pass
    assert audit.kinds() == ["request", "decision"]
    assert audit.events[-1][2] is approved


def test_broker_exception_still_records_a_decision():
    class _Exploding:
        def request(self, **kw):
            raise RuntimeError("broker died")

    audit = _RecordingAudit()
    with pytest.raises(RuntimeError):
        _gate(audit, _Exploding()).check("nova_shard_forget", OPERATOR, "s1")
    assert audit.kinds() == ["request", "decision"]
    assert audit.events[-1][2] is False


# ── pre-supplied approval (the MCP elicitation path) ─────────────────────────

def _async_check(gate, tool, approval):
    return asyncio.run(
        gate.async_check(tool, OPERATOR, "s1", "shard_a", approval=approval)
    )


def test_supplied_approval_bypasses_the_broker():
    """On the MCP path the operator is asked before the handler body runs, so
    the fallback broker must not be consulted a second time."""
    broker = _FakeBroker(answer=False)   # would refuse if consulted
    audit = _RecordingAudit()
    request_id = _async_check(_gate(audit, broker), "nova_shard_forget", True)
    assert request_id is not None
    assert broker.calls == []
    assert audit.kinds() == ["request", "decision"]
    assert audit.events[-1][2] is True


def test_supplied_refusal_raises_without_consulting_the_broker():
    broker = _FakeBroker(answer=True)    # would approve if consulted
    with pytest.raises(HITLDenied):
        _async_check(_gate(_RecordingAudit(), broker), "nova_shard_forget", False)
    assert broker.calls == []


def test_async_falls_back_to_the_broker_when_no_approval_supplied():
    broker = _FakeBroker(answer=True)
    _async_check(_gate(_RecordingAudit(), broker), "nova_shard_forget", None)
    assert len(broker.calls) == 1


def test_async_read_tool_needs_no_approval():
    assert asyncio.run(
        _gate().async_check("nova_shard_get", OPERATOR, "s1")
    ) is None


# ── the approval resolver ────────────────────────────────────────────────────

class _Caps:
    def __init__(self, elicitation):
        self.elicitation = elicitation


class _Ctx:
    def __init__(self, elicitation=True):
        self.client_capabilities = _Caps(object() if elicitation else None)


class _Params:
    shard_id = "nova_target_001"


def test_resolver_asks_for_a_destructive_tool():
    from mcp.server.mcpserver import Elicit
    from approval import approval_for

    outcome = asyncio.run(approval_for("nova_shard_forget")(_Ctx(), _Params()))
    assert isinstance(outcome, Elicit)
    assert "nova_shard_forget" in outcome.message
    assert "nova_target_001" in outcome.message   # the prompt names the target


def test_resolver_does_not_ask_for_a_safe_tool():
    from approval import ApprovalDecision, approval_for

    outcome = asyncio.run(approval_for("nova_shard_get")(_Ctx(), _Params()))
    assert isinstance(outcome, ApprovalDecision) and outcome.approve is True


def test_resolver_denies_when_the_client_cannot_be_asked():
    """Degrade to a refusal the handler can turn into a typed reject, rather
    than the SDK's missing-capability protocol error."""
    from approval import ApprovalDecision, approval_for

    outcome = asyncio.run(approval_for("nova_shard_forget")(_Ctx(elicitation=False), _Params()))
    assert isinstance(outcome, ApprovalDecision) and outcome.approve is False


@pytest.mark.parametrize("answer", [True, False])
def test_was_approved_reads_an_accepted_outcome(answer):
    from mcp.server.mcpserver import AcceptedElicitation
    from approval import ApprovalDecision, was_approved

    outcome = AcceptedElicitation(data=ApprovalDecision(approve=answer))
    assert was_approved(outcome) is answer


def test_was_approved_treats_decline_and_cancel_as_refusal():
    from mcp.server.mcpserver import CancelledElicitation, DeclinedElicitation
    from approval import was_approved

    assert was_approved(DeclinedElicitation()) is False
    assert was_approved(CancelledElicitation()) is False
    assert was_approved(None) is False
