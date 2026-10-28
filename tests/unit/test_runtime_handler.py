"""Unit tests for runtime/handler.py (Agent Invoker Lambda, OPS-005+006).

Covers: idempotent-replay short-circuit (cached result returned, control loop not
re-invoked), lock-retry-with-backoff on CaseLockHeld (3 attempts then re-raise, plus
the succeeds-on-retry path), and _summarize()'s shape. ``handle_event`` and
``case_store`` are stubbed throughout -- this exercises the Lambda's own orchestration
logic, not the control loop or a live DynamoDB.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from opsresolve.agents.orchestrator import InvocationResult
from opsresolve.common import case_store
from opsresolve.runtime import handler

RAW_TELEMETRY_EVENT = {
    "source": "tcs.opsresolve.telemetry",
    "detail-type": "Telemetry Anomaly Detected",
    "detail": {"eventId": "evt-1", "caseId": "SR-1", "correlationId": "corr-1"},
}


def _result(**overrides) -> InvocationResult:
    defaults = dict(
        case_id="SR-1",
        correlation_id="corr-1",
        from_state="OPEN",
        to_state="CONTEXT_READY",
        plan=None,
        escalated=False,
        escalation_reason=None,
        committed=True,
    )
    defaults.update(overrides)
    return InvocationResult(**defaults)


# --- Idempotent replay short-circuit -----------------------------------------------------


def test_idempotent_replay_returns_cached_result_without_calling_handle_event():
    cached = {"caseId": "SR-1", "toState": "CONTEXT_READY", "committed": True, "toolCallCount": 2}
    with (
        patch.object(handler.case_store, "check_event_processed", return_value=cached) as mock_check,
        patch.object(handler, "handle_event") as mock_handle_event,
        patch.object(handler.case_store, "mark_event_processed") as mock_mark,
    ):
        result = handler._process_one(RAW_TELEMETRY_EVENT)

    mock_check.assert_called_once_with("evt-1")
    mock_handle_event.assert_not_called()
    mock_mark.assert_not_called()  # already-processed event isn't re-recorded
    assert result == cached


def test_no_cached_result_falls_through_to_control_loop_and_caches_outcome():
    with (
        patch.object(handler.case_store, "check_event_processed", return_value=None),
        patch.object(handler, "handle_event", return_value=_result()) as mock_handle_event,
        patch.object(handler.case_store, "mark_event_processed") as mock_mark,
    ):
        result = handler._process_one(RAW_TELEMETRY_EVENT)

    mock_handle_event.assert_called_once()
    mock_mark.assert_called_once()
    marked_event_id, marked_summary = mock_mark.call_args.args
    assert marked_event_id == "evt-1"
    assert marked_summary == result
    assert result["caseId"] == "SR-1"
    assert result["toState"] == "CONTEXT_READY"


def test_event_with_no_event_id_processes_without_dedup_or_caching():
    raw_event = {
        "source": "tcs.opsresolve.telemetry",
        "detail-type": "Telemetry Anomaly Detected",
        "detail": {"caseId": "SR-1"},  # no eventId
    }
    with (
        patch.object(handler.case_store, "check_event_processed") as mock_check,
        patch.object(handler, "handle_event", return_value=_result()),
        patch.object(handler.case_store, "mark_event_processed") as mock_mark,
    ):
        handler._process_one(raw_event)

    mock_check.assert_not_called()
    mock_mark.assert_not_called()


# --- Lock-retry-with-backoff on CaseLockHeld --------------------------------------------


def test_lock_retry_gives_up_after_3_attempts_and_reraises():
    lock_exc = case_store.CaseLockHeld("Case SR-1 is locked by another invocation")
    with (
        patch.object(handler, "handle_event", side_effect=lock_exc) as mock_handle_event,
        patch.object(handler.time, "sleep") as mock_sleep,
    ):
        with pytest.raises(case_store.CaseLockHeld):
            handler._handle_with_lock_retry(RAW_TELEMETRY_EVENT, _fake_normalized())

    assert mock_handle_event.call_count == handler.LOCK_RETRY_ATTEMPTS == 3
    # Backoff sleeps happen between attempts, not after the last (failed) one.
    assert mock_sleep.call_count == 2
    mock_sleep.assert_any_call(handler.LOCK_RETRY_BACKOFF_SECONDS * 1)
    mock_sleep.assert_any_call(handler.LOCK_RETRY_BACKOFF_SECONDS * 2)


def test_lock_retry_succeeds_after_transient_contention():
    lock_exc = case_store.CaseLockHeld("locked")
    success = _result()
    with (
        patch.object(handler, "handle_event", side_effect=[lock_exc, success]) as mock_handle_event,
        patch.object(handler.time, "sleep") as mock_sleep,
    ):
        result = handler._handle_with_lock_retry(RAW_TELEMETRY_EVENT, _fake_normalized())

    assert result is success
    assert mock_handle_event.call_count == 2
    mock_sleep.assert_called_once_with(handler.LOCK_RETRY_BACKOFF_SECONDS * 1)


def test_lock_retry_succeeds_on_first_attempt_without_sleeping():
    success = _result()
    with (
        patch.object(handler, "handle_event", return_value=success) as mock_handle_event,
        patch.object(handler.time, "sleep") as mock_sleep,
    ):
        result = handler._handle_with_lock_retry(RAW_TELEMETRY_EVENT, _fake_normalized())

    assert result is success
    mock_handle_event.assert_called_once()
    mock_sleep.assert_not_called()


def _fake_normalized():
    from opsresolve.runtime.normalize import normalize_event

    return normalize_event(RAW_TELEMETRY_EVENT)


# --- _summarize() shape -------------------------------------------------------------------


def test_summarize_shape_committed_result():
    result = _result(
        case_id="SR-9",
        correlation_id="corr-9",
        from_state="DIAGNOSING",
        to_state="PLANNING_FIELD",
        escalated=False,
        escalation_reason=None,
        committed=True,
    )
    summary = handler._summarize(result)
    assert summary == {
        "caseId": "SR-9",
        "correlationId": "corr-9",
        "fromState": "DIAGNOSING",
        "toState": "PLANNING_FIELD",
        "escalated": False,
        "escalationReason": None,
        "committed": True,
        "toolCallCount": 0,
    }


def test_summarize_shape_escalated_result_with_tool_calls():
    from opsresolve.agents.orchestrator import ToolCallRecord

    result = _result(
        to_state="ESCALATED_HUMAN",
        escalated=True,
        escalation_reason="Unrecoverable error: boom",
        committed=True,
    )
    result.tool_calls = [
        ToolCallRecord(specialist="DiagnosticAgent", tool="getAssetHealth", result={}),
        ToolCallRecord(specialist="DiagnosticAgent", tool="executeSafeRemoteAction", result={}),
    ]
    summary = handler._summarize(result)
    assert summary["escalated"] is True
    assert summary["escalationReason"] == "Unrecoverable error: boom"
    assert summary["toolCallCount"] == 2
    # summary is flat/JSON-safe -- no nested Plan/ToolCallRecord objects
    assert all(isinstance(v, (str, int, bool, type(None))) for v in summary.values())


def test_summarize_is_json_serializable():
    import json

    result = _result()
    json.dumps(handler._summarize(result))  # raises if not JSON-safe
