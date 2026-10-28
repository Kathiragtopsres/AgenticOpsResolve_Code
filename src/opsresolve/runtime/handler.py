"""Agent Invoker Lambda entry point (Section 5.3, OPS-005).

This is the real target for the 8 EventBridge rules documented in ``resource.md``
(all currently on CW Logs placeholders -- swapping the rule targets to this Lambda is
a deployed-resource change tracked separately, deliberately not done as part of this
build; see resource.md's OPS-005 note).

Responsibilities, in order, per invocation:
  1. Normalize the raw EventBridge event (``runtime.normalize.normalize_event``) --
     reject anything that isn't one of the 8 deployed shapes.
  2. Check event-level idempotency (``case_store.check_event_processed``) -- EventBridge
     is at-least-once, and a Lambda invocation can itself be retried after a transient
     failure past the point where the case was already committed; short-circuit on replay.
  3. Hand off to the orchestrator control loop (``agents.orchestrator.handle_event``),
     retrying a bounded number of times with backoff if another concurrent invocation
     currently holds the case lock (``case_store.CaseLockHeld``) -- a 60s lease can
     easily span an EventBridge redelivery window, so a short in-Lambda retry avoids
     failing (and triggering yet another redelivery) on ordinary contention.
  4. Record the outcome against the eventId for future idempotent replay.

Errors from the orchestrator itself are *not* caught here. ``handle_event``'s own
fail-safe (Section 5.3) already converts in-loop errors into a committed
ESCALATED_HUMAN transition and returns normally; an exception escaping ``handle_event``
means something below the fail-safe is broken (e.g. DynamoDB unreachable), and the
right behaviour is to let the Lambda invocation fail so EventBridge's retry policy
(and eventually a DLQ) handles it -- swallowing it here would hide infra failures as
silent no-ops.
"""
from __future__ import annotations

import time
from typing import Any

from ..agents.orchestrator import InvocationResult, handle_event
from ..common import case_store, tenants
from ..common.logging import get_logger
from .normalize import NormalizedEvent, UnrecognizedEventShape, normalize_event

logger = get_logger(__name__)

# Lock-contention retry (Section 5.3 Load step: case lock, 60s lease). A handful of
# short retries is enough to ride out ordinary contention between two events on the
# same case without extending the Lambda's own timeout significantly.
LOCK_RETRY_ATTEMPTS = 3
LOCK_RETRY_BACKOFF_SECONDS = 2.0


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any] | None:
    """Lambda entry point. One EventBridge event per invocation (direct Lambda
    target, no batching) -- ``context`` is accepted for signature compatibility but
    unused today.
    """
    return _process_one(event)


def _process_one(raw_event: dict[str, Any]) -> dict[str, Any] | None:
    tenants.reset_cache()
    try:
        normalized = normalize_event(raw_event)
    except UnrecognizedEventShape as exc:
        logger.error(f"Rejecting event: {exc}")
        raise

    if normalized.event_id:
        cached = case_store.check_event_processed(normalized.event_id)
        if cached is not None:
            logger.info(
                f"Idempotent replay: eventId={normalized.event_id} already processed; "
                f"returning cached result, control loop not re-run."
            )
            return cached
    else:
        logger.error(
            f"Event has no eventId (source={normalized.source!r}, "
            f"detail-type={normalized.detail_type!r}); cannot dedupe, processing anyway."
        )

    result = _handle_with_lock_retry(raw_event, normalized)

    summary = _summarize(result)
    if normalized.event_id:
        case_store.mark_event_processed(normalized.event_id, summary)
    return summary


def _handle_with_lock_retry(raw_event: dict[str, Any], normalized: NormalizedEvent) -> InvocationResult:
    last_exc: case_store.CaseLockHeld | None = None
    for attempt in range(1, LOCK_RETRY_ATTEMPTS + 1):
        try:
            return handle_event(raw_event, case_id=normalized.case_id)
        except case_store.CaseLockHeld as exc:
            last_exc = exc
            if attempt < LOCK_RETRY_ATTEMPTS:
                logger.info(
                    f"Case lock held (attempt {attempt}/{LOCK_RETRY_ATTEMPTS}) for "
                    f"caseId={normalized.case_id}; retrying after backoff."
                )
                time.sleep(LOCK_RETRY_BACKOFF_SECONDS * attempt)
    assert last_exc is not None
    logger.error(
        f"Case lock still held after {LOCK_RETRY_ATTEMPTS} attempts for "
        f"caseId={normalized.case_id}; giving up, letting EventBridge redeliver."
    )
    raise last_exc


def _summarize(result: InvocationResult) -> dict[str, Any]:
    """Cacheable summary of an InvocationResult (Section 12.1 trace fields).

    Kept flat/JSON-safe for storage as the idempotency table's cached response --
    ``Plan``/``ToolCallRecord`` objects aren't stored verbatim, just their identifying
    fields, since the cached value only needs to answer "what happened" on replay, not
    reconstruct the full in-memory result.
    """
    return {
        "caseId": result.case_id,
        "correlationId": result.correlation_id,
        "fromState": result.from_state,
        "toState": result.to_state,
        "escalated": result.escalated,
        "escalationReason": result.escalation_reason,
        "committed": result.committed,
        "toolCallCount": len(result.tool_calls),
    }
