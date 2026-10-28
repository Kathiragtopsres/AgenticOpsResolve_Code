"""EventBridge event model (Section 8.1).

Single custom bus `opsresolve-bus`. Every event uses the standard envelope
(Listing 8-1). Sources and detail-types are enumerated here so producers and
consumers stay aligned.
"""
from __future__ import annotations

from enum import Enum

BUS_NAME = "opsresolve-bus"


class EventSource(str, Enum):
    TELEMETRY = "tcs.opsresolve.telemetry"
    CASE = "tcs.opsresolve.case"
    CHANNEL = "tcs.opsresolve.channel"
    WORKFORCE = "tcs.opsresolve.workforce"
    INVENTORY = "tcs.opsresolve.inventory"
    TIMER = "tcs.opsresolve.timer"
    APPROVAL = "tcs.opsresolve.approval"
    FSM = "tcs.opsresolve.fsm"


class DetailType(str, Enum):
    TELEMETRY_ANOMALY_DETECTED = "Telemetry Anomaly Detected"
    CASE_OPENED = "Case Opened"
    CASE_STATE_CHANGED = "Case State Changed"
    CASE_CLOSED = "Case Closed"
    CUSTOMER_MESSAGE_RECEIVED = "Customer Message Received"
    CUSTOMER_ACTION_SELECTED = "Customer Action Selected"
    MESSAGE_DELIVERY_STATUS = "Message Delivery Status"
    TECHNICIAN_ETA_UPDATED = "Technician ETA Updated"
    PART_PICK_FAILED = "Part Pick Failed"
    TIMER_FIRED = "Timer Fired"
    APPROVAL_REQUESTED = "Approval Requested"
    APPROVAL_DECIDED = "Approval Decided"
    WORK_ORDER_COMPLETED = "Work Order Completed"


def build_envelope(source: EventSource, detail_type: DetailType, detail: dict) -> dict:
    """Construct a standard EventBridge PutEvents entry (Listing 8-1).

    The ``detail`` must include eventId, eventVersion, occurredAt, caseId,
    correlationId, actor and payload. Validation is enforced by the JSON Schema
    at ``schemas/events/event-envelope.schema.json``.
    """
    return {
        "Source": source.value,
        "DetailType": detail_type.value,
        "EventBusName": BUS_NAME,
        "Detail": detail,
    }
