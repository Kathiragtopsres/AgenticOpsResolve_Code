"""Deterministic (DIRECT) goal execution for the control loop (DD3).

A ``Goal`` with ``mode="DIRECT"`` calls its Gateway tool(s) with exactly ``Goal.inputs``,
bypassing the specialist's LLM. Used for consent-bound actions whose arguments come from a
verified event (book the slot the customer tapped, reserve the part a human approved, send
the CSAT request) and whose structured result the loop branches on.

``resolve_inputs`` also serves AGENT goals: it replaces the ``"$primaryContactId"``
sentinel with the case customer's primary contact from the mock-contacts table.

Import-safe without the Strands/AgentCore SDKs: ``gateway_client`` is imported lazily.
"""
from __future__ import annotations

import time
from typing import Any

from ..common import dynamo, tenants
from ..common.config import settings
from ..common.logging import get_logger
from ..tools import workforce
from .base import tool_call_record
from .planner import (
    ASSET_MODEL,
    CUSTOMER_REGION,
    DEFAULT_REGION,
    JOB_DURATION_MIN,
    PRIMARY_CONTACT,
    RANKED_TECHNICIANS,
    REQUIRED_SKILL,
    Goal,
)
from .scheduling import DEFAULT_JOB_DURATION_MIN

logger = get_logger(__name__)

# Fallback skill per asset model when the tenant has no model profile (mock-technicians ``skills`` keys).
REQUIRED_SKILL_BY_MODEL: dict[str, str] = {
    "CX-500": "HVAC-CHILLER-L2",
    "AHU-90": "HVAC-AHU-L2",
    "ONT-X200": "FIBRE-INSTALL",
    "MRX-3T": "MRI-CRYO-L3",
}


# Local (in-process) tools a DIRECT goal may also call: the loop needs their structured result.
LOCAL_DIRECT_TOOLS: dict[str, Any] = {"findQualifiedTechnicians": workforce.find_qualified_technicians}


def ranked_technician_ids(envelope: dict) -> list[str]:
    """Technician ids from the matching step's result in ``envelope['priorResults']``, best first."""
    for prior in envelope.get("priorResults") or []:
        if prior.get("specialist") != "TechnicianMatchingAgent":
            continue
        result = (prior.get("result") or {}).get("results", {}).get("findQualifiedTechnicians") or {}
        return [c["technicianId"] for c in result.get("candidates") or [] if c.get("technicianId")]
    return []


def gateway_tool_names() -> tuple[str, ...]:
    from . import gateway_client

    return gateway_client.GATEWAY_TOOL_NAMES


def _gateway_call(name: str, arguments: dict) -> dict:
    from . import gateway_client

    return gateway_client.call_gateway_tool(name, arguments)


def call_gateway(name: str, arguments: dict) -> dict:
    """Call one Gateway tool directly (for the orchestrator's own tools, e.g. requestApproval)."""
    return _gateway_call(name, arguments)


def primary_contact_id(customer_id: str | None) -> str | None:
    """Bare contactId of the customer's primary contact (else the first by contactId)."""
    if not customer_id:
        return None
    contacts = dynamo.scan_table(
        settings.mock_contacts_table, filter_fn=lambda item: item.get("customerId") == customer_id
    )
    if not contacts:
        return None
    contacts.sort(key=lambda c: str(c.get("contactId") or c.get("pk") or ""))
    chosen = next((c for c in contacts if c.get("isPrimary") is True), contacts[0])
    contact_id = chosen.get("contactId") or str(chosen.get("pk") or "").split("#", 1)[-1]
    return contact_id or None


def asset_model(asset_id: str | None) -> str | None:
    """Model of the asset (mock-assets), or None."""
    if not asset_id:
        return None
    row = dynamo.get_item(settings.mock_assets_table, f"ASSETS#{asset_id}")
    return (row or {}).get("model") or None


def resolve_inputs(inputs: dict, envelope: dict, correlation_id: str) -> dict:
    """Copy of ``inputs`` with the sentinels (primary contact, asset model, required skill)
    resolved (dropped when they can't be) and ``caseId``/``correlationId`` filled from the
    envelope when missing."""
    resolved: dict[str, Any] = {}
    for key, value in (inputs or {}).items():
        if value in (ASSET_MODEL, REQUIRED_SKILL):
            asset_id = (envelope.get("asset") or {}).get("assetId")
            try:
                model = asset_model(asset_id)
            except Exception as exc:  # no table access: let the tool report the missing arg
                logger.error(f"Asset model lookup failed for asset {asset_id}: {exc}")
                model = None
            if value == ASSET_MODEL:
                resolved_value = model
            else:
                profile = tenants.model_profile(envelope.get("tenantId"), model) or {}
                resolved_value = profile.get("requiredSkill") or REQUIRED_SKILL_BY_MODEL.get(model or "")
            if resolved_value:
                resolved[key] = resolved_value
            continue
        if value == JOB_DURATION_MIN:
            asset_id = (envelope.get("asset") or {}).get("assetId")
            try:
                profile = tenants.model_profile(envelope.get("tenantId"), asset_model(asset_id)) or {}
            except Exception as exc:
                logger.error(f"Job duration lookup failed for asset {asset_id}: {exc}")
                profile = {}
            resolved[key] = int(profile.get("jobDurationMin") or DEFAULT_JOB_DURATION_MIN)
            continue
        if value == CUSTOMER_REGION:
            customer_id = (envelope.get("customer") or {}).get("customerId")
            try:
                customer = dynamo.get_item(settings.mock_customers_table, f"CUSTOMERS#{customer_id}")
            except Exception as exc:
                logger.error(f"Customer region lookup failed for customer {customer_id}: {exc}")
                customer = None
            resolved[key] = (
                (customer or {}).get("region")
                or tenants.tenant(envelope.get("tenantId")).get("defaultRegion")
                or DEFAULT_REGION
            )
            continue
        if value == RANKED_TECHNICIANS:
            ids = ranked_technician_ids(envelope)
            if ids:
                resolved[key] = ids
            continue
        if value == PRIMARY_CONTACT:
            customer_id = (envelope.get("customer") or {}).get("customerId")
            try:
                contact_id = primary_contact_id(customer_id)
            except Exception as exc:  # no table access: let the tool report the missing arg
                logger.error(f"Primary contact lookup failed for customer {customer_id}: {exc}")
                contact_id = None
            if contact_id:
                resolved[key] = contact_id
            continue
        resolved[key] = value
    if not resolved.get("caseId") and envelope.get("caseId"):
        resolved["caseId"] = envelope["caseId"]
    if not resolved.get("correlationId") and correlation_id:
        resolved["correlationId"] = correlation_id
    return resolved


def run_direct_goal(goal: Goal, envelope: dict, correlation_id: str) -> dict:
    """Call each of the goal's tools on the Gateway with the goal's inputs.

    Returns ``{"results": {tool: result}, "toolCalls": [...]}`` plus ``"error"`` (the first tool
    error) when any tool failed. ``toolCalls`` holds one trace record per call (input, output,
    latency, status). Raises ValueError if a tool is not a Gateway tool.
    """
    allowed = gateway_tool_names()
    not_gateway = [t for t in goal.expected_tools if t not in allowed and t not in LOCAL_DIRECT_TOOLS]
    if not_gateway:
        raise ValueError(
            f"DIRECT goals may only call Gateway tools (or {sorted(LOCAL_DIRECT_TOOLS)}); got {not_gateway}"
        )

    arguments = dict(goal.inputs)
    if not arguments.get("caseId") and envelope.get("caseId"):
        arguments["caseId"] = envelope["caseId"]
    if not arguments.get("correlationId") and correlation_id:
        arguments["correlationId"] = correlation_id

    results: dict[str, Any] = {}
    tool_calls: list[dict] = []
    first_error = None
    for tool_name in goal.expected_tools:
        local = LOCAL_DIRECT_TOOLS.get(tool_name)
        started = time.perf_counter()
        result = local(arguments) if local else _gateway_call(tool_name, arguments)
        tool_calls.append(tool_call_record(tool_name, arguments, result, int((time.perf_counter() - started) * 1000)))
        results[tool_name] = result
        if first_error is None and isinstance(result, dict) and "error" in result:
            first_error = result["error"]
    outcome: dict[str, Any] = {"results": results, "toolCalls": tool_calls}
    if first_error is not None:
        outcome["error"] = first_error
    return outcome
