"""Case store persistence layer (Section 9.2, Section 5.3 Load/Commit steps).

Single-table design on ``opsresolve-<env>-cases``: every item for a case shares
``pk`` = ``CASE#<caseId>``, discriminated by ``sk``:

  META               case metadata: state, version, priority, customerId, assetId,
                     restoreBy, channelActive, createdAt, closedAt, kpis
  EVT#<ts>#<eventId> timeline event
  PLAN#v<n>          plan version
  MSG#<ts>#<msgId>   customer/agent message
  (approvals live in opsresolve-<env>-approvals, not here -- APR# retired)
  APPT#<workOrderId> appointment record

GSI1 (status board): gsi1pk=STATE#<state>, gsi1sk=<priority>#<restoreBy>.
GSI2 (asset history): gsi2pk=ASSET#<assetId>, gsi2sk=<createdAt>.

Case locking and idempotency live on ``opsresolve-<env>-idempotency`` (Section 5.3 Load
step: "acquire a case lock — DynamoDB conditional write with 60 s lease").

This module is the thing OPS-002's case-lifecycle code and OPS-005's orchestrator both
sit on top of; it replaces the raw NotImplementedError stubs in
``src/opsresolve/tools/case.py`` for getCaseContext/transitionCaseState.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from ..engines.state_machine import IllegalTransition, transition
from . import tenants
from .config import settings
from .dynamo import get_table

CASE_LOCK_LEASE_SECONDS = 60
IDEMPOTENCY_TTL_HOURS = 24


class CaseLockHeld(Exception):
    """Raised when a case lock is already held by another invocation."""


class CaseNotFound(Exception):
    """Raised when getCaseContext is called for a caseId that doesn't exist."""


class ConcurrentModification(Exception):
    """Raised when transitionCaseState's conditional write loses a race (version mismatch)."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ddb_safe(value: Any) -> Any:
    """Recursively convert floats to Decimal: DynamoDB rejects float. Tool results parsed from JSON
    (e.g. Gateway ``valueUsd: 420.0``) reach the timeline and idempotency store through here."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: ddb_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [ddb_safe(v) for v in value]
    return value


def _epoch(seconds_from_now: int) -> int:
    return int(time.time()) + seconds_from_now


# --- Case locking (Section 5.3 Load step) ---------------------------------------------


def acquire_case_lock(case_id: str, owner: str, lease_seconds: int = CASE_LOCK_LEASE_SECONDS) -> None:
    """Acquire (or refresh, if held by the same owner) a 60 s-lease lock on a case.

    Uses a conditional put on the idempotency table so concurrent events on the same
    case serialise. Raises CaseLockHeld if another owner holds a live lease.
    """
    table = get_table(settings.idempotency_table)
    now = int(time.time())
    try:
        table.put_item(
            Item={
                "pk": f"LOCK#{case_id}",
                "sk": "META",
                "owner": owner,
                "expiresAt": now + lease_seconds,
                "ttl": _epoch(lease_seconds + 5),
            },
            ConditionExpression=(
                "attribute_not_exists(pk) OR expiresAt < :now OR #owner = :owner"
            ),
            ExpressionAttributeNames={"#owner": "owner"},
            ExpressionAttributeValues={":now": now, ":owner": owner},
        )
    except Exception as exc:  # botocore.exceptions.ClientError, kept generic for import-safety
        if _is_conditional_check_failure(exc):
            raise CaseLockHeld(f"Case {case_id} is locked by another invocation") from exc
        raise


def release_case_lock(case_id: str, owner: str) -> None:
    """Release a case lock held by ``owner``. No-op if already expired/released."""
    table = get_table(settings.idempotency_table)
    try:
        table.delete_item(
            Key={"pk": f"LOCK#{case_id}", "sk": "META"},
            ConditionExpression="#owner = :owner",
            ExpressionAttributeNames={"#owner": "owner"},
            ExpressionAttributeValues={":owner": owner},
        )
    except Exception as exc:
        if not _is_conditional_check_failure(exc):
            raise


# --- Idempotency (Section 6.5) ---------------------------------------------------------


def check_idempotency(idempotency_key: str) -> dict[str, Any] | None:
    """Return the stored response for a previously-seen idempotency key, or None."""
    table = get_table(settings.idempotency_table)
    resp = table.get_item(Key={"pk": f"IDEMP#{idempotency_key}", "sk": "META"})
    item = resp.get("Item")
    return item.get("response") if item else None


def store_idempotency_result(idempotency_key: str, response: dict[str, Any]) -> None:
    """Record a write tool's response against its idempotency key (24h TTL)."""
    table = get_table(settings.idempotency_table)
    table.put_item(
        Item={
            "pk": f"IDEMP#{idempotency_key}",
            "sk": "META",
            "response": ddb_safe(response),
            "ttl": _epoch(IDEMPOTENCY_TTL_HOURS * 3600),
        }
    )


def check_event_processed(event_id: str) -> dict[str, Any] | None:
    """Return the stored result for a previously-processed EventBridge eventId, or None.

    Distinct key prefix (``IDEMP#EVT#<eventId>``) from tool-call idempotency keys
    (``IDEMP#<key>``) so a caller-chosen idempotency key can never collide with a raw
    eventId. Backed by the same table/TTL as ``check_idempotency`` -- this is the
    Agent Invoker Lambda's guard against EventBridge's at-least-once delivery (and its
    own retries) re-running the control loop for an event it already committed.
    """
    return check_idempotency(f"EVT#{event_id}")


def mark_event_processed(event_id: str, result: dict[str, Any]) -> None:
    """Record that an EventBridge eventId has been fully processed (24h TTL)."""
    store_idempotency_result(f"EVT#{event_id}", result)


def consume_postback_nonce(nonce: str) -> bool:
    """Atomically mark a signed-postback nonce as consumed. Returns True the first time
    it's called for a given nonce, False on any replay (Listing 7-1 validation)."""
    table = get_table(settings.idempotency_table)
    try:
        table.put_item(
            Item={"pk": f"NONCE#{nonce}", "sk": "META", "ttl": _epoch(24 * 3600)},
            ConditionExpression="attribute_not_exists(pk)",
        )
        return True
    except Exception as exc:
        if _is_conditional_check_failure(exc):
            return False
        raise


# --- Case metadata + envelope assembly (Section 5.5, Section 9.2) ----------------------


@dataclass
class CaseMetadata:
    case_id: str
    state: str
    version: int
    priority: str | None
    customer_id: str | None
    asset_id: str | None
    restore_by: str | None
    correlation_id: str
    created_at: str
    closed_at: str | None = None


def open_case(
    case_id: str,
    *,
    customer_id: str,
    asset_id: str,
    correlation_id: str,
    trigger: dict,
    priority: str | None = None,
    restore_by: str | None = None,
    extras: dict | None = None,
) -> CaseMetadata:
    """Create a new case at state OPEN (Section 8.2 entry point). Idempotent on caseId:
    if the case already exists, the existing metadata is returned unchanged.

    ``extras`` are additional META attributes (e.g. ``title``, ``customerDescription``);
    None values are dropped and the core keys below always win."""
    table = get_table(settings.cases_table)
    now = _now_iso()
    item = {
        **{k: v for k, v in (extras or {}).items() if v is not None},
        "pk": f"CASE#{case_id}",
        "sk": "META",
        "caseId": case_id,
        "state": "OPEN",
        "version": 1,
        "priority": priority,
        "customerId": customer_id,
        "assetId": asset_id,
        "restoreBy": restore_by,
        "correlationId": correlation_id,
        "createdAt": now,
        "closedAt": None,
        "gsi1pk": "STATE#OPEN",
        "gsi1sk": f"{priority or 'P4'}#{restore_by or now}",
        "gsi2pk": f"ASSET#{asset_id}",
        "gsi2sk": now,
    }
    try:
        table.put_item(Item=item, ConditionExpression="attribute_not_exists(pk)")
    except Exception as exc:
        if not _is_conditional_check_failure(exc):
            raise
        existing = table.get_item(Key={"pk": f"CASE#{case_id}", "sk": "META"}).get("Item")
        if existing:
            return _metadata_from_item(existing)
        raise
    append_timeline_event(
        case_id,
        event_type="CASE_OPENED",
        actor={"type": "SYSTEM", "name": "CaseOpener"},
        summary="Case opened",
        correlation_id=correlation_id,
        detail={"trigger": trigger},
    )
    return _metadata_from_item(item)


def find_open_case_for_asset(asset_id: str) -> str | None:
    """caseId of the newest non-CLOSED/CANCELLED case on an asset (GSI2, META items only), else None.

    Known gap, accepted: two events in the same second can both pass this check before
    either case is written."""
    from boto3.dynamodb.conditions import Key

    table = get_table(settings.cases_table)
    kwargs: dict[str, Any] = {
        "IndexName": "GSI2",
        "KeyConditionExpression": Key("gsi2pk").eq(f"ASSET#{asset_id}"),
        "ScanIndexForward": False,
    }
    while True:
        resp = table.query(**kwargs)
        for item in resp.get("Items", []):
            if item.get("sk") == "META" and item.get("state") not in ("CLOSED", "CANCELLED"):
                return item["caseId"]
        if "LastEvaluatedKey" not in resp:
            return None
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


_PROTECTED_META_KEYS = frozenset({"pk", "sk", "caseId", "state", "version", "gsi1pk", "gsi1sk", "gsi2pk", "gsi2sk"})


def update_case_fields(case_id: str, fields: dict[str, Any]) -> None:
    """SET descriptive META attributes (e.g. ``subcategory``) on an existing case.

    Never changes state or ``version`` (so it cannot invalidate the control loop's optimistic
    lock) and refuses key/state/index attributes. None values are skipped."""
    values = {k: v for k, v in fields.items() if v is not None}
    bad = sorted(set(values) & _PROTECTED_META_KEYS)
    if bad:
        raise ValueError(f"update_case_fields may not set {bad}")
    if not values:
        return
    names = {f"#f{i}": key for i, key in enumerate(values)}
    exprs = {f":f{i}": ddb_safe(value) for i, value in enumerate(values.values())}
    table = get_table(settings.cases_table)
    table.update_item(
        Key={"pk": f"CASE#{case_id}", "sk": "META"},
        UpdateExpression="SET " + ", ".join(f"#f{i} = :f{i}" for i in range(len(values))),
        ConditionExpression="attribute_exists(pk)",
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=exprs,
    )


def trigger_already_merged(case_id: str, event_id: str | None) -> bool:
    """True if a TRIGGER_MERGED timeline event for this trigger ``eventId`` is already on the case.

    A lock-blocked event is retried (and EventBridge can redeliver), and each attempt reaches the
    merge path again: this keeps it to one TRIGGER_MERGED entry per trigger."""
    if not event_id:
        return False
    from boto3.dynamodb.conditions import Attr, Key

    table = get_table(settings.cases_table)
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("pk").eq(f"CASE#{case_id}") & Key("sk").begins_with("EVT#"),
        "FilterExpression": Attr("type").eq("TRIGGER_MERGED") & Attr("detail.trigger.eventId").eq(event_id),
    }
    while True:
        resp = table.query(**kwargs)
        if resp.get("Items"):
            return True
        if "LastEvaluatedKey" not in resp:
            return False
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def merged_recently(case_id: str, within_seconds: int = 900) -> bool:
    """True if a TRIGGER_MERGED timeline event was written on the case in the last ``within_seconds``.

    A telemetry producer streams one anomaly per reading; this keeps the timeline to one merge entry
    per window instead of one per anomaly."""
    from boto3.dynamodb.conditions import Attr, Key

    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=within_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
    table = get_table(settings.cases_table)
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("pk").eq(f"CASE#{case_id}") & Key("sk").between(f"EVT#{cutoff}", "EVT#~"),
        "FilterExpression": Attr("type").eq("TRIGGER_MERGED"),
    }
    while True:
        resp = table.query(**kwargs)
        if resp.get("Items"):
            return True
        if "LastEvaluatedKey" not in resp:
            return False
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def get_case_metadata(case_id: str) -> CaseMetadata:
    table = get_table(settings.cases_table)
    item = table.get_item(Key={"pk": f"CASE#{case_id}", "sk": "META"}).get("Item")
    if item is None:
        raise CaseNotFound(case_id)
    return _metadata_from_item(item)


def _metadata_from_item(item: dict) -> CaseMetadata:
    return CaseMetadata(
        case_id=item["caseId"],
        state=item["state"],
        version=int(item["version"]),
        priority=item.get("priority"),
        customer_id=item.get("customerId"),
        asset_id=item.get("assetId"),
        restore_by=item.get("restoreBy"),
        correlation_id=item.get("correlationId", ""),
        created_at=item.get("createdAt", ""),
        closed_at=item.get("closedAt"),
    )


def _live_appointment(appointments: list[dict]) -> dict | None:
    """The newest appointment that is not cancelled (else the newest): a rebooked case has several APPT# rows."""
    live = [a for a in appointments if a.get("status") != "CANCELLED"]
    return max(live or appointments, key=lambda a: str(a.get("createdAt", "")), default=None)


def get_case_context(case_id: str, *, trigger: dict, autonomy_profile: str = "DEFAULT_L2") -> dict:
    """Assemble the Case Context Envelope (Section 5.5) from the case store.

    Reads META plus the most recent items of each sk-prefixed kind, trimmed to the
    token budget (last 15 timeline events verbatim; older ones summarised — Section 5.1).
    """
    meta = get_case_metadata(case_id)
    table = get_table(settings.cases_table)

    from boto3.dynamodb.conditions import Key

    resp = table.query(KeyConditionExpression=Key("pk").eq(f"CASE#{case_id}"))
    items = resp.get("Items", [])

    plans = sorted(
        (i for i in items if i["sk"].startswith("PLAN#")), key=lambda i: i["sk"]
    )
    events = sorted(
        (i for i in items if i["sk"].startswith("EVT#")), key=lambda i: i["sk"]
    )
    appointments = [i for i in items if i["sk"].startswith("APPT#")]

    recent_events = events[-15:]
    summarised_count = max(0, len(events) - 15)
    recent_timeline = (
        ([f"...{summarised_count} earlier events summarised..."] if summarised_count else [])
        + [e.get("summary", e["sk"]) for e in recent_events]
    )

    open_waits = [
        {"type": "APPROVAL", "name": a["approvalId"]} for a in _pending_approvals(case_id)
    ]

    # Lifecycle flags the planner decides on (CSAT score, verification window, one-time customer question).
    meta_item = next((i for i in items if i["sk"] == "META"), {})
    lifecycle = {
        key: meta_item[key]
        for key in ("csatScore", "verifyWindowElapsedAt", "clarifyAsked")
        if meta_item.get(key) is not None
    }

    return {
        "caseId": meta.case_id,
        "correlationId": meta.correlation_id,
        "state": meta.state,
        "trigger": trigger,
        "tenantId": meta_item.get("tenantId") or tenants.DEFAULT_TENANT,
        "customer": {"customerId": meta.customer_id},
        "asset": {"assetId": meta.asset_id} if meta.asset_id else None,
        "entitlement": None,
        "diagnosis": None,
        "plan": plans[-1] if plans else None,
        "appointment": _live_appointment(appointments),
        "openWaits": open_waits,
        "lifecycle": lifecycle,
        "recentTimeline": recent_timeline,
        "autonomyProfile": autonomy_profile,
        "_version": meta.version,
    }


# --- State transitions (Section 8.2, Section 5.3 Commit step) --------------------------


def transition_case_state(
    case_id: str,
    target_state: str,
    *,
    expected_version: int,
    correlation_id: str,
    actor: dict | None = None,
    reason: str | None = None,
) -> CaseMetadata:
    """Validate and commit a state transition using a DynamoDB conditional expression
    on (state, version) so illegal or concurrent transitions fail deterministically
    (Section 8.2 closing note). Raises IllegalTransition or ConcurrentModification.
    """
    meta = get_case_metadata(case_id)
    transition(meta.state, target_state)  # raises IllegalTransition if not permitted

    if meta.version != expected_version:
        raise ConcurrentModification(
            f"Case {case_id} version mismatch: expected {expected_version}, got {meta.version}"
        )

    table = get_table(settings.cases_table)
    now = _now_iso()
    from ..common.enums import TERMINAL_STATES

    update_expr = "SET #state = :state, version = :newver, gsi1pk = :gsi1pk"
    expr_values: dict[str, Any] = {
        ":state": target_state,
        ":oldver": expected_version,
        ":newver": expected_version + 1,
        ":gsi1pk": f"STATE#{target_state}",
    }
    if target_state in {s.value for s in TERMINAL_STATES}:
        update_expr += ", closedAt = :closedAt"
        expr_values[":closedAt"] = now

    try:
        table.update_item(
            Key={"pk": f"CASE#{case_id}", "sk": "META"},
            UpdateExpression=update_expr,
            ConditionExpression="#state = :oldstate AND version = :oldver",
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues={**expr_values, ":oldstate": meta.state},
        )
    except Exception as exc:
        if _is_conditional_check_failure(exc):
            raise ConcurrentModification(
                f"Case {case_id} was modified concurrently; retry"
            ) from exc
        raise

    append_timeline_event(
        case_id,
        event_type="CASE_STATE_CHANGED",
        actor=actor or {"type": "SYSTEM", "name": "Orchestrator"},
        summary=f"{meta.state} -> {target_state}" + (f" ({reason})" if reason else ""),
        correlation_id=correlation_id,
        detail={"from": meta.state, "to": target_state, "reason": reason},
    )
    return get_case_metadata(case_id)


# --- Timeline / plan / message / approval / appointment writes -------------------------


def append_timeline_event(
    case_id: str,
    *,
    event_type: str,
    actor: dict,
    summary: str,
    correlation_id: str,
    detail: dict | None = None,
    tool_name: str | None = None,
    policy_decision: str | None = None,
) -> str:
    """Append an immutable timeline event (Section 9.2, Section 12.1 trace fields)."""
    table = get_table(settings.cases_table)
    ts = _now_iso()
    event_id = f"evt-{uuid.uuid4().hex[:12]}"
    table.put_item(
        Item={
            "pk": f"CASE#{case_id}",
            "sk": f"EVT#{ts}#{event_id}",
            "type": event_type,
            "actor": actor,
            "summary": summary,
            "toolName": tool_name,
            "policyDecision": policy_decision,
            "correlationId": correlation_id,
            "detail": ddb_safe(detail or {}),
        }
    )
    return event_id


def save_plan(case_id: str, version: int, goals: list[dict], rationale: str, prompt_version: str) -> None:
    table = get_table(settings.cases_table)
    table.put_item(
        Item={
            "pk": f"CASE#{case_id}",
            "sk": f"PLAN#v{version}",
            "goals": ddb_safe(goals),
            "rationale": rationale,
            "promptVersion": prompt_version,
        }
    )


def save_appointment(
    case_id: str, work_order_id: str, *, technician_id: str, slot_start: str, slot_end: str, status: str
) -> None:
    table = get_table(settings.cases_table)
    table.put_item(
        Item={
            "pk": f"CASE#{case_id}",
            "sk": f"APPT#{work_order_id}",
            "technicianId": technician_id,
            "slotStart": slot_start,
            "slotEnd": slot_end,
            "status": status,
        }
    )


def _pending_approvals(case_id: str) -> list[dict]:
    """PENDING approvals for a case from ``opsresolve-<env>-approvals`` (Lakshmi's
    requestApproval table: hash key ``approvalId``, attrs caseId/action/amount/
    approverRole/status/createdAt; no GSI, so scan + filter).

    Scan ``Limit`` applies before the filter, so we don't set one: we follow
    ``LastEvaluatedKey`` until exhausted. Fine at demo volume. The old ``APR#`` items
    in the cases table are retired (no writer remains).
    """
    from boto3.dynamodb.conditions import Attr

    table = get_table(settings.approvals_table)
    kwargs: dict[str, Any] = {
        "FilterExpression": Attr("caseId").eq(case_id) & Attr("status").eq("PENDING")
    }
    found: list[dict] = []
    while True:
        resp = table.scan(**kwargs)
        found.extend(resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    return sorted(found, key=lambda a: a.get("createdAt", ""))


def _is_conditional_check_failure(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if not response:
        return False
    return response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"
