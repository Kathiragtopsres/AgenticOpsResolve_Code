"""Unit tests for runtime/normalize.py (OPS-005+006, Section 5.3 Interpret step).

Covers: ROUTING_TABLE completeness against the 8 deployed EventBridge rules / 13
detail-types (resource.md OPS-002), normalize_event() happy path per shape, and
UnrecognizedEventShape on anything not in the table.
"""
from __future__ import annotations

import pytest

from opsresolve.runtime.normalize import (
    ROUTING_TABLE,
    NormalizedEvent,
    UnrecognizedEventShape,
    normalize_event,
)

# The 8 deployed EventBridge rules (resource.md OPS-002) and every detail-type each one
# matches. 13 detail-types total across the 8 rules -- this is the ground truth the
# ROUTING_TABLE must cover exactly, no more, no less.
DETAIL_TYPES_BY_RULE: dict[str, list[str]] = {
    "telemetry": ["Telemetry Anomaly Detected"],
    "case": ["Case Opened", "Case State Changed", "Case Closed"],
    "channel": ["Customer Message Received", "Customer Action Selected", "Message Delivery Status"],
    "workforce": ["Technician ETA Updated"],
    "inventory": ["Part Pick Failed"],
    "timer": ["Timer Fired"],
    "approval": ["Approval Requested", "Approval Decided"],
    "fsm": ["Work Order Completed"],
}
ALL_DETAIL_TYPES = [dt for group in DETAIL_TYPES_BY_RULE.values() for dt in group]

EXPECTED_TRIGGER_TYPE: dict[str, str] = {
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
    "Case Opened": "CASE_LIFECYCLE",
    "Case State Changed": "CASE_LIFECYCLE",
    "Case Closed": "CASE_LIFECYCLE",
}


def test_all_13_detail_types_across_8_rules_accounted_for():
    assert len(ALL_DETAIL_TYPES) == 13
    assert len(EXPECTED_TRIGGER_TYPE) == 13


def test_routing_table_covers_exactly_the_13_deployed_detail_types():
    assert set(ROUTING_TABLE) == set(ALL_DETAIL_TYPES)


def test_routing_table_trigger_types_match_expected_mapping():
    for detail_type, expected_trigger in EXPECTED_TRIGGER_TYPE.items():
        assert ROUTING_TABLE[detail_type] == expected_trigger


@pytest.mark.parametrize("detail_type", ALL_DETAIL_TYPES)
def test_normalize_event_happy_path_per_shape(detail_type):
    raw_event = {
        "source": "tcs.opsresolve.some-source",
        "detail-type": detail_type,
        "detail": {
            "eventId": "evt-123",
            "caseId": "SR-ABC",
            "correlationId": "corr-123",
        },
    }
    result = normalize_event(raw_event)
    assert isinstance(result, NormalizedEvent)
    assert result.trigger_type == EXPECTED_TRIGGER_TYPE[detail_type]
    assert result.detail_type == detail_type
    assert result.event_id == "evt-123"
    assert result.case_id == "SR-ABC"
    assert result.correlation_id == "corr-123"


def test_normalize_event_case_id_optional_for_case_opening_trigger():
    raw_event = {
        "source": "tcs.opsresolve.telemetry",
        "detail-type": "Telemetry Anomaly Detected",
        "detail": {"eventId": "evt-1"},
    }
    result = normalize_event(raw_event)
    assert result.case_id is None
    assert result.correlation_id == "evt-1"  # falls back to eventId when correlationId absent


def test_normalize_event_correlation_id_falls_back_to_empty_string_when_no_ids():
    raw_event = {
        "source": "tcs.opsresolve.telemetry",
        "detail-type": "Telemetry Anomaly Detected",
        "detail": {},
    }
    result = normalize_event(raw_event)
    assert result.event_id is None
    assert result.correlation_id == ""


def test_normalize_event_accepts_detailType_camelcase_key():
    raw_event = {
        "source": "tcs.opsresolve.timer",
        "detailType": "Timer Fired",
        "detail": {"eventId": "evt-9"},
    }
    result = normalize_event(raw_event)
    assert result.trigger_type == "TIMER"


def test_unrecognized_event_shape_raises_on_unknown_detail_type():
    raw_event = {
        "source": "tcs.opsresolve.telemetry",
        "detail-type": "Something Made Up",
        "detail": {"eventId": "evt-1"},
    }
    with pytest.raises(UnrecognizedEventShape, match="Something Made Up"):
        normalize_event(raw_event)


def test_unrecognized_event_shape_raises_on_missing_detail_type():
    raw_event = {"source": "tcs.opsresolve.telemetry", "detail": {"eventId": "evt-1"}}
    with pytest.raises(UnrecognizedEventShape):
        normalize_event(raw_event)
