"""Case domain tools (Section 6.2, Listings 6-4, 5.4.1).

  checkServiceEntitlement  (Read, Mock CRM contracts)
  executeSafeRemoteAction  (Write, Device management simulator, idempotent; allow-list only)
  generateServiceReport    (Write, Report Lambda -> HTML/PDF to S3, idempotent)
  closeServiceCase         (Write, Case store + Mock FSM, idempotent; verification required)
  getCaseContext           (Read, Case store -> Case Context Envelope)
  transitionCaseState      (Write, DynamoDB conditional expression on state+version)
  scheduleTimer            (Write, EventBridge Scheduler)

checkServiceEntitlement is implemented against the mock-contracts table (OPS-003).
getCaseContext and transitionCaseState delegate to the case store persistence layer
(src/opsresolve/common/case_store.py, OPS-005). executeSafeRemoteAction,
generateServiceReport and closeServiceCase stay integration points: they depend on
downstream services (device simulator, report generator) that are OPS-006 scope.
"""
from __future__ import annotations

from ..common import case_store, dynamo
from ..common.config import settings
from ..common.enums import ToolErrorCode
from .base import error_envelope


def check_service_entitlement(payload: dict) -> dict:
    """Determine coverage, SLA tier, priority and customer cost for an asset's contract.

    Mirrors schemas/tools/checkServiceEntitlement.schema.json. Input: {caseId, assetId,
    issueType?}. Rules evaluate coverage deterministically (Section 5.4.5); the LLM layer
    (SLA and Entitlement Agent) only turns this into a plain-language explanation.
    """
    asset_id = payload.get("assetId")
    correlation_id = payload.get("correlationId", payload.get("caseId", ""))
    if not asset_id or not payload.get("caseId"):
        return error_envelope(
            ToolErrorCode.VALIDATION_ERROR, "caseId and assetId are required", correlation_id
        )

    contracts = dynamo.scan_table(
        settings.mock_contracts_table,
        filter_fn=lambda item: asset_id in item.get("assetIds", []),
    )
    if not contracts:
        return {
            "covered": False,
            "coverageType": "NONE",
            "slaTier": None,
            "restoreBy": None,
            "priority": "P4",
            "customerCostUsd": None,
        }

    contract = contracts[0]
    coverage = contract.get("coverage", {})
    covered = bool(coverage.get("parts")) and bool(coverage.get("labour"))
    sla_tier = contract.get("slaTier")

    # SLA tier -> priority mapping (Section 5.4.5 / 8.4). Restore-by hours come straight
    # from the contract; the caller (Diagnostic/Entitlement agent) anchors restoreBy to
    # either the predicted-failure time or the case-open time depending on trigger type.
    priority_by_tier = {
        "GOLD_4H": "P2",
        "PREMIUM_UPTIME": "P1",
        "BIZ_NEXT_DAY": "P3",
    }
    priority = priority_by_tier.get(sla_tier, "P3")

    return {
        "covered": covered,
        "coverageType": "WARRANTY_PARTS_LABOUR" if covered else "NONE",
        "slaTier": sla_tier,
        "restoreHours": contract.get("restoreHours"),
        "priority": priority,
        "customerCostUsd": 0 if covered else None,
        "contractId": contract.get("contractId"),
        "autoApprovePartUsd": contract.get("autoApprovePartUsd"),
    }


def execute_safe_remote_action(payload: dict) -> dict:  # pragma: no cover
    raise NotImplementedError


def generate_service_report(payload: dict) -> dict:  # pragma: no cover
    raise NotImplementedError


def close_service_case(payload: dict) -> dict:  # pragma: no cover
    raise NotImplementedError


def get_case_context(payload: dict) -> dict:
    """Read the Case Context Envelope for a case (Section 5.5).

    Input: {caseId, trigger, autonomyProfile?}. Delegates to case_store.get_case_context.
    """
    case_id = payload.get("caseId")
    correlation_id = payload.get("correlationId", "")
    if not case_id:
        return error_envelope(ToolErrorCode.VALIDATION_ERROR, "caseId is required", correlation_id)
    try:
        return case_store.get_case_context(
            case_id,
            trigger=payload.get("trigger", {}),
            autonomy_profile=payload.get("autonomyProfile", "DEFAULT_L2"),
        )
    except case_store.CaseNotFound:
        return error_envelope(ToolErrorCode.NOT_FOUND, f"Unknown caseId {case_id}", correlation_id)


def transition_case_state(payload: dict) -> dict:
    """Transition a case to a new state via a DynamoDB conditional expression on
    (state, version) (Section 8.2). Input: {caseId, targetState, expectedVersion,
    correlationId, actor?, reason?}.
    """
    case_id = payload.get("caseId")
    target_state = payload.get("targetState")
    expected_version = payload.get("expectedVersion")
    correlation_id = payload.get("correlationId", "")
    if not case_id or not target_state or expected_version is None:
        return error_envelope(
            ToolErrorCode.VALIDATION_ERROR,
            "caseId, targetState and expectedVersion are required",
            correlation_id,
        )
    try:
        meta = case_store.transition_case_state(
            case_id,
            target_state,
            expected_version=expected_version,
            correlation_id=correlation_id,
            actor=payload.get("actor"),
            reason=payload.get("reason"),
        )
    except case_store.CaseNotFound:
        return error_envelope(ToolErrorCode.NOT_FOUND, f"Unknown caseId {case_id}", correlation_id)
    except case_store.ConcurrentModification as exc:
        return error_envelope(ToolErrorCode.CONFLICT_STATE, str(exc), correlation_id)
    except Exception as exc:  # engines.state_machine.IllegalTransition
        if exc.__class__.__name__ == "IllegalTransition":
            return error_envelope(ToolErrorCode.CONFLICT_STATE, str(exc), correlation_id)
        raise
    return {"caseId": case_id, "state": meta.state, "version": meta.version}


def schedule_timer(payload: dict) -> dict:
    """Create a one-time EventBridge Scheduler schedule named opsresolve-<env>-sched-<caseId>-<purpose>
    (Section 8.6), targeting the opsresolve-<env>-timers group provisioned in OPS-002.

    Input: {caseId, purpose, fireAt (ISO-8601 UTC), detail?}. The schedule's target is
    a PutEvents call onto the opsresolve bus with detail-type "Timer Fired" so the
    Agent Invoker Lambda (OPS-005 runtime, once deployed) picks it up like any other
    event; no direct Lambda invoke is used, keeping timers on the same event path as
    everything else (Architecture Principle AP-2).
    """
    case_id = payload.get("caseId")
    purpose = payload.get("purpose")
    fire_at = payload.get("fireAt")
    correlation_id = payload.get("correlationId", "")
    if not case_id or not purpose or not fire_at:
        return error_envelope(
            ToolErrorCode.VALIDATION_ERROR,
            "caseId, purpose and fireAt are required",
            correlation_id,
        )

    # Always starts with "opsresolve-": the console Reset only ever deletes schedules with that prefix.
    schedule_name = f"{settings.prefix}-sched-{case_id}-{purpose}"
    fire_at_expr = f"at({fire_at.rstrip('Z')})" if not fire_at.startswith("at(") else fire_at

    import json as _json

    detail = {
        "eventId": f"evt-{schedule_name}",
        "eventVersion": "1.0",
        "caseId": case_id,
        "correlationId": correlation_id,
        "actor": {"type": "SYSTEM", "name": "EventBridgeScheduler"},
        "payload": {"purpose": purpose, **(payload.get("detail") or {})},
    }

    client = _scheduler_client()
    client.create_schedule(
        Name=schedule_name,
        GroupName=f"{settings.prefix}-timers",
        ScheduleExpression=fire_at_expr,
        FlexibleTimeWindow={"Mode": "OFF"},
        Target={
            "Arn": _events_put_events_target_arn(),
            "RoleArn": _scheduler_invocation_role_arn(),
            "EventBridgeParameters": {
                "DetailType": "Timer Fired",
                "Source": "tcs.opsresolve.timer",
            },
            "Input": _json.dumps(detail),
        },
        ActionAfterCompletion="DELETE",
    )
    return {"scheduleName": schedule_name, "fireAt": fire_at, "status": "SCHEDULED"}


def _scheduler_client():  # pragma: no cover - thin AWS SDK wrapper
    import boto3

    return boto3.client("scheduler", region_name=settings.region)


def _events_put_events_target_arn() -> str:
    """EventBridge Scheduler's native target for PutEvents is the bus ARN itself."""
    import boto3

    sts = boto3.client("sts", region_name=settings.region)
    account_id = sts.get_caller_identity()["Account"]
    return f"arn:aws:events:{settings.region}:{account_id}:event-bus/{settings.prefix}-bus"


def _scheduler_invocation_role_arn() -> str:
    """IAM role EventBridge Scheduler assumes to call PutEvents on the bus.

    TODO(OPS-002 follow-up): a narrow single-purpose role for this (scheduler.amazonaws.com
    trust, events:PutEvents on the opsresolve bus only) has not been created yet — this is
    the same class of narrow role as opsresolve-dev-iot-telemetry-rule-role. Needs a real
    ARN wired here (and logged to resource.md) before scheduleTimer can run end-to-end.
    """
    import boto3

    sts = boto3.client("sts", region_name=settings.region)
    account_id = sts.get_caller_identity()["Account"]
    return f"arn:aws:iam::{account_id}:role/{settings.prefix}-scheduler-invocation-role"
