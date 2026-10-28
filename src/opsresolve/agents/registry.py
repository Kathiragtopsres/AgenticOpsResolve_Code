"""Specialist agent registry (Section 5.2, Section 5.4).

Maps specialist name -> SpecialistSpec so the orchestrator can route plan goals to the
right specialist without importing every specialist module by hand. This is the
"agents-as-tools" catalogue: each specialist is exposed to the Orchestrator as a named
tool via base.build_agent (Section 5.1) + Strands' ``Agent.as_tool()``.

``SPECIALISTS`` (name -> SpecialistSpec) is available with no SDK dependency, so the
deterministic control loop and its tests can keep routing against it without ever
touching Strands. ``build_specialist_agents`` / ``specialist_tools_for_orchestrator`` are
the SDK-dependent half: they lazily build a real Strands ``Agent`` per specialist and
wrap each as a named tool for the Orchestrator's own ``Agent``.
"""
from __future__ import annotations

from typing import Any

from . import (
    asset_context,
    communication,
    completion,
    diagnostic,
    entitlement,
    knowledge,
    parts,
    scheduling,
    service_recovery,
    technician_matching,
)
from .base import SpecialistSpec

SPECIALISTS: dict[str, SpecialistSpec] = {
    spec.name: spec
    for spec in (
        asset_context.SPEC,
        diagnostic.SPEC,
        knowledge.SPEC,
        entitlement.SPEC,
        parts.SPEC,
        technician_matching.SPEC,
        scheduling.SPEC,
        service_recovery.SPEC,
        communication.SPEC,
        completion.SPEC,
    )
}


def get_specialist(name: str) -> SpecialistSpec:
    """Look up a specialist by name; raises KeyError with the valid names on typos."""
    try:
        return SPECIALISTS[name]
    except KeyError:
        raise KeyError(
            f"Unknown specialist {name!r}. Valid: {sorted(SPECIALISTS)}"
        ) from None


def specialists_for_tool(tool_name: str) -> list[str]:
    """Return the names of specialists whose tool set includes ``tool_name``.

    Used by the orchestrator to sanity-check that a planned tool call is actually
    owned by the specialist the plan assigned it to (Section 5.4.1: "cannot call write
    tools that a specialist owns").
    """
    return [name for name, spec in SPECIALISTS.items() if tool_name in spec.tools]


# Cache of built Strands Agent instances, keyed by specialist name. Built lazily (not at
# import time) so this module stays importable without the Strands SDK / a live Bedrock
# model for anything that only needs SPECIALISTS/get_specialist/specialists_for_tool
# (unit tests, the deterministic control loop).
_AGENT_CACHE: dict[str, Any] = {}


def build_specialist_agents(*, force_rebuild: bool = False) -> dict[str, Any]:
    """Build (or return cached) Strands ``Agent`` instances for all 10 specialists.

    Each agent is built via ``base.build_agent(spec)``, which resolves the specialist's
    tool list against the local tool registry (Gateway-only tool names are omitted until
    the Gateway client is wired -- Section "Next up" in tasks.md). Results are cached
    per-process; pass ``force_rebuild=True`` to rebuild (e.g. after config changes).
    """
    from .base import build_agent

    if force_rebuild:
        _AGENT_CACHE.clear()
    for name, spec in SPECIALISTS.items():
        if name not in _AGENT_CACHE:
            _AGENT_CACHE[name] = build_agent(spec)
    return _AGENT_CACHE


# Tool name each specialist should be exposed under to the Orchestrator, and the
# docstring the Orchestrator's model sees when deciding whether to delegate to it.
# Mirrors SpecialistSpec.purpose but phrased as a tool description (imperative, what
# calling this tool does) rather than a spec description.
_ORCHESTRATOR_TOOL_ALIAS = {
    "AssetContextAgent": "asset_context_agent",
    "DiagnosticAgent": "diagnostic_agent",
    "KnowledgeAgent": "knowledge_agent",
    "SlaEntitlementAgent": "sla_entitlement_agent",
    "PartsAgent": "parts_agent",
    "TechnicianMatchingAgent": "technician_matching_agent",
    "SchedulingDispatchAgent": "scheduling_dispatch_agent",
    "ServiceRecoveryAgent": "service_recovery_agent",
    "CommunicationAgent": "communication_agent",
    "CompletionAgent": "completion_agent",
}


def _make_specialist_tool(agent_name: str, tool_name: str, purpose: str):
    """Build one @tool-decorated wrapper closing over a specialist's built Agent.

    Strands' installed version (1.27.x) has no ``Agent.as_tool()`` helper, so this
    reproduces it by hand: a plain function that invokes the specialist ``Agent`` with a
    text instruction and returns its text response, decorated with ``@tool`` so the
    Orchestrator's own ``Agent`` sees it as a callable tool (the "Passing agents
    directly as tools" pattern in the Strands docs, made explicit instead of relying on
    the SDK's automatic conversion, so each tool keeps the specialist's real name and
    purpose instead of a generic description).
    """
    from strands import tool

    def _call(instruction: str) -> str:
        agent = build_specialist_agents()[agent_name]
        result = agent(instruction)
        return str(result)

    _call.__name__ = tool_name
    _call.__doc__ = (
        f"{purpose}\n\nArgs:\n    instruction: what to ask this specialist to do, "
        "including the relevant case context."
    )
    return tool(_call, name=tool_name, description=purpose)


def specialist_tools_for_orchestrator() -> list[Any]:
    """Build every specialist agent and wrap each as a named Strands tool.

    This is the "agents-as-tools" list the Orchestrator's own ``Agent`` is constructed
    with (Section 5.2 topology): the Orchestrator only ever sees these 10 tools plus its
    own direct tools (getCaseContext, transitionCaseState, scheduleTimer,
    requestApproval); it never calls a specialist's underlying tools directly.
    """
    build_specialist_agents()  # ensure all 10 are built (and cached) up front
    return [
        _make_specialist_tool(name, _ORCHESTRATOR_TOOL_ALIAS[name], SPECIALISTS[name].purpose)
        for name in SPECIALISTS
    ]
