"""Local tool registry for Strands wiring (Section 5.1, 5.4).

Maps each of the 10 local tool names (in-process, no AgentCore Gateway hop -- see
context.md "Agent deployment model") to a Strands ``@tool``-decorated callable so
``agents.base.build_agent`` can resolve a ``SpecialistSpec.tools`` list into actual
tools without every specialist module importing the Lambda-adapter layer directly.

The underlying adapters (``tools/case.py``, ``tools/context.py``, ``tools/workforce.py``)
already take/return plain dicts per the JSON Schemas in ``schemas/tools/*``; each wrapper
here just gives Strands a typed, documented function signature (dict in, dict out) so the
model gets a usable tool spec. Gateway tools (13, net-new, behind AgentCore Gateway) are
intentionally absent from this registry -- see LOCAL_TOOL_NAMES for the exact set covered
today and context.md for why the rest stay out until Lakshmi's Lambda + schema land.
"""
from __future__ import annotations

from strands import tool

from ..tools import case as case_tools
from ..tools import context as context_tools
from ..tools import workforce as workforce_tools

# The 10 local tools already implemented in-process (tasks.md "Local tools" list).
LOCAL_TOOL_NAMES: tuple[str, ...] = (
    "getAssetContext",
    "getServiceHistory",
    "getAssetHealth",
    "getContactPreferences",
    "checkServiceEntitlement",
    "getCaseContext",
    "transitionCaseState",
    "scheduleTimer",
    "findQualifiedTechnicians",
    "getTechnicianStatus",
)


@tool(name="getAssetContext")
def get_asset_context(payload: dict) -> dict:
    """Assemble customer, site, asset, contract and recent-work-order context for an asset.

    Args:
        payload: {"assetId": str, "caseId"?: str, "correlationId"?: str}
    """
    return context_tools.get_asset_context(payload)


@tool(name="getServiceHistory")
def get_service_history(payload: dict) -> dict:
    """Return prior work orders for an asset, most recent first.

    Args:
        payload: {"assetId": str, "correlationId"?: str}
    """
    return context_tools.get_service_history(payload)


@tool(name="getAssetHealth")
def get_asset_health(payload: dict) -> dict:
    """Return latest telemetry sample, trend and normal-band status for an asset.

    Args:
        payload: {"assetId": str, "windowMin"?: int, "correlationId"?: str}
    """
    return context_tools.get_asset_health(payload)


@tool(name="getContactPreferences")
def get_contact_preferences(payload: dict) -> dict:
    """Return a contact's preferred channel and consent record.

    Args:
        payload: {"contactId": str, "correlationId"?: str}
    """
    return context_tools.get_contact_preferences(payload)


@tool(name="checkServiceEntitlement")
def check_service_entitlement(payload: dict) -> dict:
    """Determine coverage, SLA tier, priority and customer cost for an asset's contract.

    Args:
        payload: {"caseId": str, "assetId": str, "issueType"?: str, "correlationId"?: str}
    """
    return case_tools.check_service_entitlement(payload)


@tool(name="getCaseContext")
def get_case_context(payload: dict) -> dict:
    """Read the Case Context Envelope for a case.

    Args:
        payload: {"caseId": str, "trigger"?: dict, "autonomyProfile"?: str, "correlationId"?: str}
    """
    return case_tools.get_case_context(payload)


@tool(name="transitionCaseState")
def transition_case_state(payload: dict) -> dict:
    """Transition a case to a new state via a conditional write on (state, version).

    Args:
        payload: {"caseId": str, "targetState": str, "expectedVersion": int,
            "correlationId"?: str, "actor"?: dict, "reason"?: str}
    """
    return case_tools.transition_case_state(payload)


@tool(name="scheduleTimer")
def schedule_timer(payload: dict) -> dict:
    """Create a one-time EventBridge Scheduler schedule that fires back onto the bus.

    Args:
        payload: {"caseId": str, "purpose": str, "fireAt": str, "detail"?: dict,
            "correlationId"?: str}
    """
    return case_tools.schedule_timer(payload)


@tool(name="findQualifiedTechnicians")
def find_qualified_technicians(payload: dict) -> dict:
    """Rank qualified technicians for a required skill, asset model and region.

    Args:
        payload: {"requiredSkill": str, "assetModel": str, "region": str,
            "regionsAllowed"?: list[str], "windowStart"?: str, "windowEnd"?: str,
            "correlationId"?: str}
    """
    return workforce_tools.find_qualified_technicians(payload)


@tool(name="getTechnicianStatus")
def get_technician_status(payload: dict) -> dict:
    """Return a technician's current location/ETA and job-progress state.

    Args:
        payload: {"technicianId": str, "correlationId"?: str}
    """
    return workforce_tools.get_technician_status(payload)


# Name -> Strands tool, in the same order as LOCAL_TOOL_NAMES.
_LOCAL_TOOLS_BY_NAME = {
    "getAssetContext": get_asset_context,
    "getServiceHistory": get_service_history,
    "getAssetHealth": get_asset_health,
    "getContactPreferences": get_contact_preferences,
    "checkServiceEntitlement": check_service_entitlement,
    "getCaseContext": get_case_context,
    "transitionCaseState": transition_case_state,
    "scheduleTimer": schedule_timer,
    "findQualifiedTechnicians": find_qualified_technicians,
    "getTechnicianStatus": get_technician_status,
}


def resolve_local_tools(tool_names: list[str]) -> list:
    """Resolve a SpecialistSpec.tools list to Strands tools, skipping unknown names.

    Unknown names are tool names owned by the AgentCore Gateway (the 13 write/external
    tools -- not yet available in-process, see context.md) rather than an error: a
    specialist's tool list can legitimately mix local and gateway tool names before the
    gateway client is wired in (Next up item in tasks.md).
    """
    return [_LOCAL_TOOLS_BY_NAME[name] for name in tool_names if name in _LOCAL_TOOLS_BY_NAME]


def missing_gateway_tools(tool_names: list[str]) -> list[str]:
    """Return the subset of tool_names not covered by the local registry.

    Useful for logging/diagnostics: these are the tool names a specialist expects but
    that currently have no callable behind them (gateway tools, not yet wired).
    """
    return [name for name in tool_names if name not in _LOCAL_TOOLS_BY_NAME]
