"""Unit tests for specialist agent routing (OPS-005+006).

Covers:
  - agents/registry.py: get_specialist (KeyError on typo, message lists valid names),
    specialists_for_tool (ownership lookup, incl. tools shared by >1 specialist).
  - agents/orchestrator.py: _act()'s routing-ownership check -- raises OrchestratorError
    when a plan's goal names a tool not owned by that specialist, *before* invoking the
    specialist (Section 5.4.1: "cannot call write tools that a specialist owns").

None of this touches Strands or a live Bedrock model: registry.SPECIALISTS/get_specialist/
specialists_for_tool are plain-Python and import-safe (see registry.py's module docstring);
_act is exercised with a stub SpecialistInvoker instead of StrandsSpecialistInvoker, and
case_store.append_timeline_event is stubbed so no DynamoDB call is made.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from opsresolve.agents import registry
from opsresolve.agents.orchestrator import InvocationResult, OrchestratorError, _act
from opsresolve.agents.planner import Goal, Plan

# --- registry.get_specialist ------------------------------------------------------------


def test_get_specialist_returns_spec_for_known_name():
    spec = registry.get_specialist("DiagnosticAgent")
    assert spec.name == "DiagnosticAgent"


def test_get_specialist_raises_keyerror_on_typo():
    with pytest.raises(KeyError, match="DiagnosticAgentt"):
        registry.get_specialist("DiagnosticAgentt")


def test_get_specialist_keyerror_lists_valid_names():
    with pytest.raises(KeyError) as exc_info:
        registry.get_specialist("NotARealAgent")
    message = str(exc_info.value)
    for name in registry.SPECIALISTS:
        assert name in message


# --- registry.specialists_for_tool -------------------------------------------------------


def test_specialists_for_tool_single_owner():
    assert registry.specialists_for_tool("checkServiceEntitlement") == ["SlaEntitlementAgent"]


def test_specialists_for_tool_unknown_tool_returns_empty():
    assert registry.specialists_for_tool("noSuchTool") == []


@pytest.mark.parametrize(
    "tool_name,expected_owners",
    [
        ("getServiceHistory", {"AssetContextAgent", "DiagnosticAgent"}),
        ("retrieveTroubleshootingKnowledge", {"DiagnosticAgent", "KnowledgeAgent"}),
        ("findQualifiedTechnicians", {"TechnicianMatchingAgent", "ServiceRecoveryAgent"}),
        ("transferPart", {"PartsAgent", "ServiceRecoveryAgent"}),
        ("proposeAppointmentSlots", {"SchedulingDispatchAgent", "ServiceRecoveryAgent"}),
        ("getAssetHealth", {"DiagnosticAgent", "CompletionAgent"}),
        ("updateWorkOrder", {"SchedulingDispatchAgent", "CompletionAgent"}),
        ("sendCustomerMessage", {"CommunicationAgent", "CompletionAgent"}),
    ],
)
def test_specialists_for_tool_multiple_owners(tool_name, expected_owners):
    assert set(registry.specialists_for_tool(tool_name)) == expected_owners


def test_every_specialist_tool_resolves_to_at_least_its_own_owner():
    """Sanity check: every tool a SpecialistSpec declares must resolve back to that
    specialist via specialists_for_tool (own-tool ownership is never dropped)."""
    for name, spec in registry.SPECIALISTS.items():
        for tool_name in spec.tools:
            assert name in registry.specialists_for_tool(tool_name)


# --- orchestrator._act's routing-ownership check -----------------------------------------


class _StubInvoker:
    """Records invocations instead of calling a real Strands Agent."""

    def __init__(self, response: dict | None = None):
        self.calls: list[tuple] = []
        self.response = response if response is not None else {"summary": "done"}

    def invoke(self, spec, goal, envelope, correlation_id):
        self.calls.append((spec, goal, envelope, correlation_id))
        return self.response


def _plan_with_goal(specialist: str, expected_tools: list[str]) -> Plan:
    return Plan(
        version=1,
        goals=[Goal(specialist, expected_tools, "test rationale")],
        target_state=None,
        rationale="test",
    )


def test_act_raises_when_tool_not_owned_by_named_specialist():
    """AssetContextAgent doesn't own executeSafeRemoteAction (that's DiagnosticAgent's) --
    a plan that misroutes it must be rejected before any specialist is invoked."""
    plan = _plan_with_goal("AssetContextAgent", ["executeSafeRemoteAction"])
    result = InvocationResult(
        case_id="SR-1", correlation_id="corr-1", from_state="OPEN", to_state="OPEN", plan=plan
    )
    invoker = _StubInvoker()

    with patch("opsresolve.agents.orchestrator.case_store.append_timeline_event") as mock_timeline:
        with pytest.raises(OrchestratorError, match="executeSafeRemoteAction"):
            _act("SR-1", {}, plan, "corr-1", result, invoker)

    assert invoker.calls == []  # fail fast: never invoked
    mock_timeline.assert_not_called()


def test_act_error_message_names_actual_owners():
    plan = _plan_with_goal("AssetContextAgent", ["checkServiceEntitlement"])
    result = InvocationResult(
        case_id="SR-1", correlation_id="corr-1", from_state="OPEN", to_state="OPEN", plan=plan
    )
    with patch("opsresolve.agents.orchestrator.case_store.append_timeline_event"):
        with pytest.raises(OrchestratorError, match="SlaEntitlementAgent"):
            _act("SR-1", {}, plan, "corr-1", result, _StubInvoker())


def test_act_invokes_specialist_when_routing_is_valid():
    plan = _plan_with_goal("AssetContextAgent", ["getAssetContext", "getServiceHistory"])
    result = InvocationResult(
        case_id="SR-1", correlation_id="corr-1", from_state="OPEN", to_state="OPEN", plan=plan
    )
    invoker = _StubInvoker(response={"summary": "context assembled"})
    envelope = {"caseId": "SR-1", "state": "OPEN"}

    with patch("opsresolve.agents.orchestrator.case_store.append_timeline_event") as mock_timeline:
        outcomes = _act("SR-1", envelope, plan, "corr-1", result, invoker)

    assert len(invoker.calls) == 1  # one invocation per goal, covering both expected tools
    spec, goal, passed_envelope, correlation_id = invoker.calls[0]
    assert spec.name == "AssetContextAgent"
    assert goal.expected_tools == ["getAssetContext", "getServiceHistory"]
    assert passed_envelope is envelope
    assert correlation_id == "corr-1"

    assert [o.tool for o in outcomes] == ["getAssetContext", "getServiceHistory"]
    assert all(o.specialist == "AssetContextAgent" for o in outcomes)
    assert all(o.result == {"summary": "context assembled"} for o in outcomes)
    assert result.tool_calls == outcomes
    mock_timeline.assert_called_once()


def test_act_validates_every_expected_tool_in_a_multi_tool_goal():
    """One legally-owned tool plus one misrouted tool in the same goal must still be
    rejected -- ownership is checked per tool, not just the first one."""
    plan = _plan_with_goal("DiagnosticAgent", ["getAssetHealth", "checkServiceEntitlement"])
    result = InvocationResult(
        case_id="SR-1", correlation_id="corr-1", from_state="OPEN", to_state="OPEN", plan=plan
    )
    with patch("opsresolve.agents.orchestrator.case_store.append_timeline_event"):
        with pytest.raises(OrchestratorError, match="checkServiceEntitlement"):
            _act("SR-1", {}, plan, "corr-1", result, _StubInvoker())
