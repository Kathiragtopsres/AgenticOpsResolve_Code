"""Normalize the 8 EventBridge shapes into one envelope (Section 5.3 Interpret step).

``resource.md`` documents 8 deployed EventBridge rules, each matching a
(source, detail-type) pair:

  telemetry  tcs.opsresolve.telemetry   Telemetry Anomaly Detected
  case       tcs.opsresolve.case        Case Opened | Case State Changed | Case Closed
  channel    tcs.opsresolve.channel     Customer Message Received | Customer Action Selected
             |                          Message Delivery Status
  workforce  tcs.opsresolve.workforce   Technician ETA Updated
  inventory  tcs.opsresolve.inventory   Part Pick Failed
  timer      tcs.opsresolve.timer       Timer Fired
  approval   tcs.opsresolve.approval    Approval Requested | Approval Decided
  fsm        tcs.opsresolve.fsm         Work Order Completed

This module is the single source of truth for that mapping. ``agents.orchestrator``
previously kept an inline copy (``TRIGGER_TYPES_BY_DETAIL_TYPE``); it now imports
``ROUTING_TABLE`` from here instead of owning its own copy.

The Agent Invoker Lambda (``runtime.handler``) is the only caller of
``normalize_event``. Everything downstream of it (the orchestrator control loop)
keeps working with the same ``{"type", "name", "eventId"}`` trigger shape it already
expects -- this module only formalizes and centralizes how that shape is derived.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# detail-type -> normalised trigger type the planner reasons over (Section 5.5).
ROUTING_TABLE: dict[str, str] = {
    "Telemetry Anomaly Detected": "TELEMETRY",
    "Customer Message Received": "CUSTOMER_REPLY",
    "Customer Action Selected": "POSTBACK",
    "Message Delivery Status": "MESSAGE_DELIVERY_STATUS",
    "Timer Fired": "TIMER",
    "Approval Requested": "APPROVAL",
    "Approval Decided": "APPROVAL",
    "Work Order Completed": "WORK_ORDER_UPDATE",
    "Technician ETA Updated": "WORK_ORDER_UPDATE",
    "Part Pick Failed": "WORK_ORDER_UPDATE",
    # Case-lifecycle events are produced by the case store itself (CASE_OPENED /
    # CASE_STATE_CHANGED timeline writes echoed onto the bus) rather than being
    # something the orchestrator needs to react to as a *trigger* -- they're kept in
    # the table so normalize_event() doesn't reject them as unrecognized, but they
    # classify to a no-op trigger type the planner has no rule for (falls through to
    # the orchestrator's existing escalate-fallback, unchanged behaviour).
    "Case Opened": "CASE_LIFECYCLE",
    "Case State Changed": "CASE_LIFECYCLE",
    "Case Closed": "CASE_LIFECYCLE",
}


class UnrecognizedEventShape(Exception):
    """Raised when an event's detail-type doesn't match any of the 8 deployed rules.

    Distinct from "the planner has no rule for this state/trigger combination" --
    that's a normal, already-handled fail-safe path (escalate). This exception means
    the Invoker received something it structurally doesn't recognise at all (e.g. a
    typo'd detail-type, a new rule added without updating ROUTING_TABLE, or a stray
    event routed to the wrong target) -- worth surfacing as a distinct operational
    signal rather than silently falling through to the same escalate path.
    """


@dataclass
class NormalizedEvent:
    """The Invoker's view of one EventBridge event, before it's handed to the
    orchestrator (which still receives the raw event -- this is what the Invoker
    itself needs to make its own decisions: idempotency key, case lookup, logging)."""

    event_id: str | None
    case_id: str | None
    trigger_type: str
    source: str
    detail_type: str
    correlation_id: str


def normalize_event(raw_event: dict[str, Any]) -> NormalizedEvent:
    """Validate and normalize one EventBridge event (the Invoker's Interpret step).

    Expects the standard EventBridge invocation shape: ``{"source", "detail-type",
    "detail": {...}}``. ``detail`` must carry at least ``eventId``; ``caseId`` may be
    absent for a case-opening trigger (the orchestrator already tolerates that --
    see ``agents.orchestrator._open_case_from_trigger``).

    Raises ``UnrecognizedEventShape`` if ``detail-type`` isn't one of the 8 deployed
    shapes in ROUTING_TABLE.
    """
    source = raw_event.get("source", "")
    detail_type = raw_event.get("detail-type") or raw_event.get("detailType") or ""
    detail = raw_event.get("detail", {})

    trigger_type = ROUTING_TABLE.get(detail_type)
    if trigger_type is None:
        raise UnrecognizedEventShape(
            f"detail-type={detail_type!r} (source={source!r}) is not one of the 8 "
            f"deployed EventBridge shapes; known detail-types: {sorted(ROUTING_TABLE)}"
        )

    return NormalizedEvent(
        event_id=detail.get("eventId"),
        case_id=detail.get("caseId"),
        trigger_type=trigger_type,
        source=source,
        detail_type=detail_type,
        correlation_id=detail.get("correlationId", detail.get("eventId", "")),
    )
