"""T5: POST /approvals/{id}/decision -- conditional PENDING -> APPROVED/REJECTED, then
``Approval Decided`` onto the bus so the Agent Invoker resumes the case.

Handler: opsresolve.console_api.approval_decision_handler.handler

Known limitation: the DynamoDB update and PutEvents are not atomic. If PutEvents fails
after the update succeeded, the approval stays decided (409 on retry) and the case is NOT
resumed; we return 502 EVENT_PUBLISH_FAILED and log loudly so it can be replayed manually.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from ..common import tenants
from ..common.events import DetailType, EventSource, build_envelope
from ..common.logging import get_logger
from . import _common as c

logger = get_logger(__name__)

_DECISIONS = {"APPROVE": c.APPROVAL_STATUS_APPROVED, "REJECT": c.APPROVAL_STATUS_REJECTED}
_MAX_REASON = 1000


@lru_cache(maxsize=1)
def _ddb():  # pragma: no cover - thin SDK wrapper
    import boto3

    return boto3.resource("dynamodb", region_name=c.settings.region)


@lru_cache(maxsize=1)
def _events():  # pragma: no cover - thin SDK wrapper
    import boto3

    return boto3.client("events", region_name=c.settings.region)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _publish(approval: dict, decision: str, status: str, username: str) -> None:
    event_id = f"evt-{uuid.uuid4().hex[:12]}"
    case_id = approval.get("caseId")
    detail = {
        "eventId": event_id,
        "eventVersion": "1.0",
        "occurredAt": approval["decidedAt"],
        "caseId": case_id,
        "correlationId": approval.get("idempotencyKey") or event_id,
        "actor": {"type": "HUMAN", "id": username},
        "payload": {
            "approvalId": approval["approvalId"],
            "decision": status,  # APPROVED | REJECTED
            "reason": approval.get("reason", ""),
            "decidedBy": username,
            "decidedAt": approval["decidedAt"],
            "action": approval.get("action"),
        },
    }
    entry = build_envelope(EventSource.APPROVAL, DetailType.APPROVAL_DECIDED, detail)
    # build_envelope hardcodes the wrong bus ("opsresolve-bus"); override explicitly.
    entry["EventBusName"] = c.BUS_NAME
    entry["Detail"] = json.dumps(detail)
    resp = _events().put_events(Entries=[entry])
    if resp.get("FailedEntryCount"):
        raise RuntimeError(f"PutEvents failed: {resp.get('Entries')}")


def _case_tenant(case_id: str | None) -> str:
    """Tenant of the approval's case (a case without a tenantId belongs to the default tenant)."""
    if not case_id:
        return tenants.DEFAULT_TENANT
    meta = _ddb().Table(c.CASES_TABLE).get_item(Key={"pk": f"CASE#{case_id}", "sk": "META"}).get("Item") or {}
    return meta.get("tenantId") or tenants.DEFAULT_TENANT


def _stock_up_requested_part(approval: dict) -> None:
    """An approved part request from the mobile parts screen adds one unit to the requesting technician's depot
    (home depot, else DEP-CHN-01 like the mobile app), so the part shows up in the app. A failure is logged and
    never blocks the decision."""
    action = str(approval.get("action") or "")
    if not action.startswith("PART_REQUEST:") or approval.get("source") != "FIELD_APP":
        return
    part_id, tech_id = action.split(":", 1)[1], approval.get("requestedBy")
    try:
        tech = _ddb().Table(c.settings.mock_technicians_table).get_item(
            Key={"pk": f"TECHNICIANS#{tech_id}", "sk": "META"}).get("Item") or {}
        depot = tech.get("homeDepotId") or "DEP-CHN-01"
        _ddb().Table(c.settings.mock_inventory_table).update_item(
            Key={"pk": f"INV#{depot}#{part_id}", "sk": "META"},
            UpdateExpression=("SET onHand = if_not_exists(onHand, :zero) + :one, "
                              "depotId = if_not_exists(depotId, :d), partId = if_not_exists(partId, :p)"),
            ExpressionAttributeValues={":zero": 0, ":one": 1, ":d": depot, ":p": part_id})
        logger.info(f"Approval {approval.get('approvalId')}: +1 {part_id} at {depot}")
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Approval {approval.get('approvalId')}: could not add {part_id} to stock: {exc}")


def handler(event: dict, context: Any = None) -> dict:
    approval_id = (event.get("pathParameters") or {}).get("id")
    if not approval_id:
        return c.error(400, "BAD_REQUEST", "Missing approval id in path")

    body = c.parse_json_body(event)
    if body is None:
        return c.error(400, "BAD_REQUEST", "Body must be a JSON object")
    decision = body.get("decision")
    if decision not in _DECISIONS:
        return c.error(400, "BAD_REQUEST", "decision must be APPROVE or REJECT")
    reason = body.get("reason", "")
    if reason is None:
        reason = ""
    if not isinstance(reason, str) or len(reason) > _MAX_REASON:
        return c.error(400, "BAD_REQUEST", f"reason must be a string up to {_MAX_REASON} chars")

    username = c.jwt_username(event)
    if not username:
        return c.error(401, "UNAUTHORIZED", "Missing authenticated user")

    caller_tenant = c.tenant_id(event)
    if not caller_tenant:
        return c.error(403, "FORBIDDEN", c.NO_TENANT_MESSAGE)

    status = _DECISIONS[decision]
    table = _ddb().Table(c.APPROVALS_TABLE)
    current = table.get_item(Key={c.APPROVAL_PK: approval_id}).get("Item")
    if current and _case_tenant(current.get("caseId")) != caller_tenant:
        return c.error(404, "NOT_FOUND", f"Approval {approval_id} not found")
    try:
        resp = table.update_item(
            Key={c.APPROVAL_PK: approval_id},
            UpdateExpression="SET #s = :new, reason = :r, decidedBy = :u, decidedAt = :t",
            ConditionExpression="attribute_exists(approvalId) AND #s = :pending",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":new": status, ":r": reason, ":u": username, ":t": _now(),
                ":pending": c.APPROVAL_STATUS_PENDING,
            },
            ReturnValues="ALL_NEW",
            ReturnValuesOnConditionCheckFailure="ALL_OLD",
        )
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "response", {}).get("Error", {}).get("Code")
        if code == "ConditionalCheckFailedException":
            old = getattr(exc, "response", {}).get("Item")
            if not old:  # fall back to a read if the SDK didn't return the item
                old = table.get_item(Key={c.APPROVAL_PK: approval_id}).get("Item")
            if not old:
                return c.error(404, "NOT_FOUND", f"Approval {approval_id} not found")
            cur = old.get("status")
            cur = cur.get("S") if isinstance(cur, dict) else cur
            return c.error(409, "NOT_PENDING", f"Approval is already {cur}")
        logger.error(f"Approval update failed for {approval_id}: {exc}")
        return c.error(500, "INTERNAL_ERROR", "Internal error")

    approval = resp["Attributes"]
    if status == c.APPROVAL_STATUS_APPROVED:
        _stock_up_requested_part(approval)
    try:
        _publish(approval, decision, status, username)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            f"EVENT_PUBLISH_FAILED: approval {approval_id} (case {approval.get('caseId')}) "
            f"is {status} in DynamoDB but 'Approval Decided' was NOT published: {exc}"
        )
        return c.error(
            502, "EVENT_PUBLISH_FAILED",
            f"Approval was recorded as {status} but the case could not be notified",
        )
    logger.info(f"Approval {approval_id} {status} by {username}")
    return c.ok(approval)
