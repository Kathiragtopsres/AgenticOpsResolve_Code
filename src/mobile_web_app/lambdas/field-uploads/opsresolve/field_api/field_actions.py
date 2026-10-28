"""Field app write Lambda: POST /mobile-web-app/jobs/{jobId}/{status, parts/used, parts/requests, complete}.

Handler: opsresolve.field_api.field_actions.handler  (HTTP API payload v2; dispatch on routeKey)

Routes (all behind the ``opsresolve-dev-field-cognito`` JWT authorizer, technician-only):
  POST /mobile-web-app/jobs/{jobId}/status          (EN_ROUTE | ON_SITE)
  POST /mobile-web-app/jobs/{jobId}/parts/used
  POST /mobile-web-app/jobs/{jobId}/parts/requests
  POST /mobile-web-app/jobs/{jobId}/complete

Every write is idempotent on the caller-supplied ``idempotencyKey`` (stored on
``opsresolve-dev-idempotency``, pk=IDEMP#FIELD#<key>). See API_requirements.md Sections 4,
6, 7, 10 for the full behavioural contract reproduced inline below.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Callable

from ..common import case_store
from ..common.case_store import CaseLockHeld, CaseNotFound, ConcurrentModification
from ..common.config import settings
from ..common.events import DetailType, EventSource, build_envelope
from ..common.logging import get_logger
from ..common.state_machine import IllegalTransition
from ..runtime.normalize import normalize_event
from . import _common as c

logger = get_logger(__name__)

TARGET_TOOLS_LAMBDA = "OpsResolve-target-tools-lambda"
_MAX_NOTE = 1000


class _HttpError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _bad(message: str) -> _HttpError:
    return _HttpError(400, "BAD_REQUEST", message)


def _conflict(message: str) -> _HttpError:
    return _HttpError(409, "CONFLICT", message)


def _not_found(message: str) -> _HttpError:
    return _HttpError(404, "NOT_FOUND", message)


def _forbidden(message: str) -> _HttpError:
    return _HttpError(403, "FORBIDDEN", message)


@lru_cache(maxsize=1)
def _ddb():  # pragma: no cover - thin SDK wrapper
    import boto3

    return boto3.resource("dynamodb", region_name=settings.region)


@lru_cache(maxsize=1)
def _events():  # pragma: no cover - thin SDK wrapper
    import boto3

    return boto3.client("events", region_name=settings.region)


@lru_cache(maxsize=1)
def _lambda():  # pragma: no cover - thin SDK wrapper
    import boto3

    return boto3.client("lambda", region_name=settings.region)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _job_id(event: dict) -> str:
    jid = (event.get("pathParameters") or {}).get("jobId") or ""
    if not jid:
        raise _bad("Missing jobId in path")
    return jid


def _idempotency_key(body: dict) -> str:
    key = body.get("idempotencyKey")
    if not isinstance(key, str) or not key:
        raise _bad("idempotencyKey is required")
    return f"FIELD#{key}"


def _case_partition(case_id: str) -> list[dict]:
    from boto3.dynamodb.conditions import Key

    resp = _ddb().Table(c.CASES_TABLE).query(KeyConditionExpression=Key("pk").eq(f"CASE#{case_id}"))
    return resp.get("Items", [])


def _appt_for_technician(case_id: str, technician_id: str) -> dict:
    items = _case_partition(case_id)
    appt = next(
        (i for i in items if str(i.get("sk", "")).startswith("APPT#") and i.get("technicianId") == technician_id
         and i.get("status") != "CANCELLED"),
        None,
    )
    if appt is None:
        raise _forbidden("job is not assigned to the caller")
    return appt


def _meta(case_id: str) -> dict:
    items = _case_partition(case_id)
    meta = next((i for i in items if i.get("sk") == "META"), None)
    if meta is None:
        raise _not_found(f"job {case_id} not found")
    return meta


def _check_idempotent(key: str) -> dict | None:
    return case_store.check_idempotency(key)


def _store_idempotent(key: str, response: dict) -> None:
    case_store.store_idempotency_result(key, response)


# --- direct-invoke of Lakshmi's OpsResolve-target-tools-lambda ----------------------------


def _invoke_tool(tool_name: str, **kwargs: Any) -> dict:
    """Direct-invoke contract (API_requirements.md Section 4): flat args at the top level,
    no ClientContext. Response is always {"statusCode": 200, "body": "<json>"}; check
    body.error since the Lambda itself never returns a non-200."""
    payload = {"tool_name": tool_name, **kwargs}
    resp = _lambda().invoke(
        FunctionName=TARGET_TOOLS_LAMBDA,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode("utf-8"),
    )
    raw = resp["Payload"].read()
    outer = json.loads(raw) if raw else {}
    body_raw = outer.get("body")
    body = json.loads(body_raw) if isinstance(body_raw, str) else (body_raw or {})
    if isinstance(body, dict) and body.get("error"):
        raise RuntimeError(f"{tool_name} failed: {body['error']}")
    return body


def _send_en_route_message(case_id: str, contact_id: str, technician_name: str, work_order_id: str, idem_suffix: str) -> dict:
    return _invoke_tool(
        "sendCustomerMessage",
        caseId=case_id,
        contactId=contact_id,
        intent="CONFIRMATION",
        data={"template": "TECHNICIAN_EN_ROUTE", "technicianName": technician_name, "workOrderId": work_order_id},
        idempotencyKey=f"{idem_suffix}:enroute",
    )


def _primary_contact(customer_id: str | None, site_id: str | None) -> dict | None:
    from boto3.dynamodb.conditions import Attr

    if not customer_id and not site_id:
        return None
    flt = None
    if customer_id:
        flt = Attr("customerId").eq(customer_id)
    if site_id:
        flt = (flt | Attr("siteId").eq(site_id)) if flt is not None else Attr("siteId").eq(site_id)
    items = _ddb().Table(settings.mock_contacts_table).scan(FilterExpression=flt).get("Items", [])
    return next((k for k in items if k.get("isPrimary") is True), items[0] if items else None)


# --- 4. POST /mobile-web-app/jobs/{jobId}/status -------------------------------------------

_APPT_STATUS_TRANSITIONS = {"EN_ROUTE": ("SCHEDULED", "DELAYED"), "ON_SITE": ("EN_ROUTE",)}


def _status(event: dict, technician_id: str) -> dict:
    case_id = _job_id(event)
    body = c.parse_json_body(event)
    if body is None:
        raise _bad("Body must be a JSON object")
    target_status = body.get("status")
    if target_status not in _APPT_STATUS_TRANSITIONS:
        raise _bad("status must be EN_ROUTE or ON_SITE")
    idem_key = _idempotency_key(body)

    cached = _check_idempotent(idem_key)
    if cached is not None:
        return cached

    appt = _appt_for_technician(case_id, technician_id)
    work_order_id = str(appt["sk"]).removeprefix("APPT#")
    current_appt_status = appt.get("status")
    allowed_from = _APPT_STATUS_TRANSITIONS[target_status]
    if current_appt_status not in allowed_from:
        raise _conflict(f"cannot move to {target_status} from {current_appt_status}")

    meta = _meta(case_id)
    now = _now_iso()
    table = _ddb().Table(c.CASES_TABLE)

    timestamp_attr = "enRouteAt" if target_status == "EN_ROUTE" else "arrivedAt"
    try:
        table.update_item(
            Key={"pk": f"CASE#{case_id}", "sk": appt["sk"]},
            UpdateExpression="SET #s = :new, #ts = :now",
            ConditionExpression="#s = :old",
            ExpressionAttributeNames={"#s": "status", "#ts": timestamp_attr},
            ExpressionAttributeValues={":new": target_status, ":old": current_appt_status, ":now": now},
        )
    except Exception as exc:  # noqa: BLE001
        if _is_conditional_failure(exc):
            raise _conflict("appointment status changed concurrently; retry") from exc
        raise

    case_store.append_timeline_event(
        case_id,
        event_type="TECHNICIAN_EN_ROUTE" if target_status == "EN_ROUTE" else "TECHNICIAN_ON_SITE",
        actor={"type": "TECHNICIAN", "name": technician_id},
        summary=f"Technician {target_status.replace('_', ' ').lower()}",
        correlation_id=meta.get("correlationId", ""),
        detail={"workOrderId": work_order_id},
    )

    customer_notified = None
    if target_status == "EN_ROUTE":
        tech = _ddb().Table(settings.mock_technicians_table).get_item(
            Key={"pk": f"TECHNICIANS#{technician_id}", "sk": "META"}
        ).get("Item") or {}
        contact = _primary_contact(meta.get("customerId"), None)
        contact_id = contact.get("contactId") if contact else None
        try:
            tool_resp = _send_en_route_message(
                case_id, contact_id or "", tech.get("name") or technician_id, work_order_id, idem_key
            )
            customer_notified = {"messageId": tool_resp.get("messageId"), "channel": tool_resp.get("channel")}
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"sendCustomerMessage failed for {case_id}: {exc}")
            customer_notified = None
        msg_body = f"Technician {tech.get('name') or technician_id} is on the way (WO {work_order_id})."
        table.put_item(Item={
            "pk": f"CASE#{case_id}", "sk": f"MSG#{now}#msg-{uuid.uuid4().hex[:12]}",
            "channel": (customer_notified or {}).get("channel") or "SMS", "direction": "OUTBOUND",
            "body": msg_body, "sentAt": now,
        })
    else:  # ON_SITE
        target_case_state = "IN_SERVICE"
        current_state = meta.get("state")
        try:
            if current_state == "AT_RISK":
                case_store.transition_case_state(
                    case_id, "SCHEDULED", expected_version=meta["version"], correlation_id=meta.get("correlationId", ""),
                    actor={"type": "TECHNICIAN", "name": technician_id}, reason="technician on site",
                )
                meta = _meta(case_id)
            case_store.transition_case_state(
                case_id, target_case_state, expected_version=meta["version"], correlation_id=meta.get("correlationId", ""),
                actor={"type": "TECHNICIAN", "name": technician_id}, reason="technician arrived on site",
            )
        except (IllegalTransition, ConcurrentModification) as exc:
            raise _conflict(str(exc)) from exc

    result = {
        "jobId": case_id, "jobStatus": target_status, "caseState": _meta(case_id).get("state"),
        "customerNotified": customer_notified,
    }
    _store_idempotent(idem_key, result)
    return result


def _is_conditional_failure(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if not response:
        return False
    return response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


# --- 6. POST /mobile-web-app/jobs/{jobId}/parts/used ---------------------------------------


def _parts_used(event: dict, technician_id: str) -> dict:
    case_id = _job_id(event)
    body = c.parse_json_body(event)
    if body is None:
        raise _bad("Body must be a JSON object")
    part_id = body.get("partId")
    qty = body.get("qty")
    if not isinstance(part_id, str) or not part_id:
        raise _bad("partId is required")
    if not isinstance(qty, int) or qty <= 0:
        raise _bad("qty must be a positive integer")
    idem_key = _idempotency_key(body)

    cached = _check_idempotent(idem_key)
    if cached is not None:
        return cached

    appt = _appt_for_technician(case_id, technician_id)
    work_order_id = str(appt["sk"]).removeprefix("APPT#")
    if c.job_status(appt.get("status")) != "ON_SITE":
        raise _conflict("job is not ON_SITE")

    meta = _meta(case_id)
    tech = _ddb().Table(settings.mock_technicians_table).get_item(
        Key={"pk": f"TECHNICIANS#{technician_id}", "sk": "META"}
    ).get("Item") or {}
    depot_id = tech.get("homeDepotId") or "DEP-CHN-01"
    used_at = _now_iso()

    case_store.append_timeline_event(
        case_id, event_type="PART_USED", actor={"type": "TECHNICIAN", "name": technician_id},
        summary=f"Part used: {part_id} x{qty}", correlation_id=meta.get("correlationId", ""),
        detail={"partId": part_id, "qty": qty, "depotId": depot_id, "workOrderId": work_order_id, "usedAt": used_at},
    )

    result = {"jobId": case_id, "partId": part_id, "qty": qty, "usedAt": used_at, "depotId": depot_id}
    _store_idempotent(idem_key, result)
    return result


# --- 7. POST /mobile-web-app/jobs/{jobId}/parts/requests ------------------------------------

# Fallback unit values until mock-parts is reseeded (API_requirements.md Section 7 note).
_FALLBACK_PART_VALUES = {"CTRL-BRD-500": 420, "CTRL-BRD-900": 1240, "PRS-SNS-55": 95}


def _part_unit_value(part_id: str) -> float:
    part = _ddb().Table(settings.mock_parts_table).get_item(Key={"pk": f"PARTS#{part_id}", "sk": "META"}).get("Item")
    if part and part.get("unitValueUsd") is not None:
        return float(part["unitValueUsd"])
    return float(_FALLBACK_PART_VALUES.get(part_id, 0))


def _parts_requests(event: dict, technician_id: str) -> dict:
    case_id = _job_id(event)
    body = c.parse_json_body(event)
    if body is None:
        raise _bad("Body must be a JSON object")
    part_id = body.get("partId")
    qty = body.get("qty")
    note = body.get("note")
    if not isinstance(part_id, str) or not part_id:
        raise _bad("partId is required")
    if not isinstance(qty, int) or qty <= 0:
        raise _bad("qty must be a positive integer")
    if note is not None and (not isinstance(note, str) or len(note) > _MAX_NOTE):
        raise _bad(f"note must be a string up to {_MAX_NOTE} chars")
    idem_key = _idempotency_key(body)

    cached = _check_idempotent(idem_key)
    if cached is not None:
        return cached

    _appt_for_technician(case_id, technician_id)  # 403 if not assigned
    meta = _meta(case_id)

    unit_value = _part_unit_value(part_id)
    amount = str(unit_value * qty)
    approval_id = f"APR-FLD-{uuid.uuid4().hex[:8]}"
    created_at = _now_iso()

    table = _ddb().Table(c.APPROVALS_TABLE)
    try:
        table.put_item(
            Item={
                "approvalId": approval_id, "caseId": case_id, "action": f"PART_REQUEST:{part_id}",
                "amount": amount, "approverRole": "SERVICE_MANAGER", "notifiedApprover": "APR-11",
                "status": "PENDING", "idempotencyKey": idem_key, "createdAt": created_at,
                "requestedBy": technician_id, "qty": qty, "source": "FIELD_APP",
                **({"note": note} if note else {}),
            },
            ConditionExpression="attribute_not_exists(approvalId)",
        )
    except Exception as exc:  # noqa: BLE001
        if not _is_conditional_failure(exc):
            raise

    case_store.append_timeline_event(
        case_id, event_type="PART_REQUESTED", actor={"type": "TECHNICIAN", "name": technician_id},
        summary=f"Part requested: {part_id} x{qty}", correlation_id=meta.get("correlationId", ""),
        detail={"partId": part_id, "qty": qty, "approvalId": approval_id},
    )

    result = {
        "approvalId": approval_id, "status": "PENDING", "caseId": case_id, "action": f"PART_REQUEST:{part_id}",
        "amount": amount, "approverRole": "SERVICE_MANAGER", "notifiedApprover": "APR-11", "createdAt": created_at,
    }
    _store_idempotent(idem_key, result)
    return result


# --- 10. POST /mobile-web-app/jobs/{jobId}/complete -----------------------------------------


def _validate_photo_keys(job_id: str, photo_keys: list[Any]) -> None:
    if not isinstance(photo_keys, list) or not all(isinstance(k, str) for k in photo_keys):
        raise _bad("photoKeys must be a list of strings")
    for key in photo_keys:
        if not key.startswith(f"field/{job_id}/"):
            raise _bad(f"photo key not under field/{job_id}/")


def _complete(event: dict, technician_id: str) -> dict:
    case_id = _job_id(event)
    body = c.parse_json_body(event)
    if body is None:
        raise _bad("Body must be a JSON object")

    checklist = body.get("checklist")
    photo_keys = body.get("photoKeys") or []
    signature_key = body.get("signatureKey")
    debrief = body.get("debrief")

    if not isinstance(checklist, list) or not checklist:
        raise _bad("checklist is required")
    for item in checklist:
        if not isinstance(item, dict) or not item.get("done"):
            raise _bad("every checklist item must be done")
    if not isinstance(signature_key, str) or not signature_key:
        raise _bad("signatureKey is required")
    if not signature_key.startswith(f"field/{case_id}/"):
        raise _bad(f"signature key not under field/{case_id}/")
    _validate_photo_keys(case_id, photo_keys)
    idem_key = _idempotency_key(body)

    cached = _check_idempotent(idem_key)
    if cached is not None:
        return cached

    appt = _appt_for_technician(case_id, technician_id)
    work_order_id = str(appt["sk"]).removeprefix("APPT#")
    if c.job_status(appt.get("status")) != "ON_SITE":
        raise _conflict("job is not ON_SITE")

    required_items = {item.get("item") for item in appt.get("checklist") or []}
    submitted_items = {item.get("item") for item in checklist}
    if required_items and required_items != submitted_items:
        raise _bad("checklist does not match the job's checklist")

    meta = _meta(case_id)
    completed_at = _now_iso()
    table = _ddb().Table(c.CASES_TABLE)

    try:
        table.update_item(
            Key={"pk": f"CASE#{case_id}", "sk": appt["sk"]},
            UpdateExpression=(
                "SET #s = :new, completedAt = :ca, checklist = :cl, photoKeys = :pk, "
                "signatureKey = :sk, debrief = :db"
            ),
            ConditionExpression="#s = :old",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":new": "COMPLETED", ":old": "ON_SITE", ":ca": completed_at, ":cl": checklist,
                ":pk": photo_keys, ":sk": signature_key, ":db": debrief,
            },
        )
    except Exception as exc:  # noqa: BLE001
        if _is_conditional_failure(exc):
            raise _conflict("job is not ON_SITE") from exc
        raise

    event_id = f"evt-{uuid.uuid4().hex[:12]}"
    case_store.append_timeline_event(
        case_id, event_type="TECHNICIAN_COMPLETED_VISIT", actor={"type": "TECHNICIAN", "name": technician_id},
        summary="Technician completed the visit", correlation_id=meta.get("correlationId", ""),
        detail={"workOrderId": work_order_id, "photoKeys": photo_keys, "signatureKey": signature_key},
    )

    parts_used = [
        (i.get("detail") or {}).get("partId")
        for i in _case_partition(case_id)
        if str(i.get("sk", "")).startswith("EVT#") and i.get("type") == "PART_USED"
    ]

    detail = {
        "eventId": event_id, "eventVersion": "1.0", "occurredAt": completed_at, "caseId": case_id,
        "correlationId": meta.get("correlationId") or event_id,
        "actor": {"type": "TECHNICIAN", "id": technician_id},
        "payload": {
            "workOrderId": work_order_id, "technicianId": technician_id, "partsUsed": parts_used,
            "debrief": debrief, "evidence": {"photoKeys": photo_keys, "signatureKey": signature_key},
        },
    }
    raw = {"source": EventSource.FSM.value, "detail-type": DetailType.WORK_ORDER_COMPLETED.value, "detail": detail}
    normalize_event(raw)  # same validation the Agent Invoker applies; raises if the shape is unroutable
    entry = build_envelope(EventSource.FSM, DetailType.WORK_ORDER_COMPLETED, detail)
    entry["EventBusName"] = c.BUS_NAME  # build_envelope hardcodes the wrong bus name; override
    entry["Detail"] = json.dumps(detail)
    resp = _events().put_events(Entries=[entry])
    if resp.get("FailedEntryCount"):
        logger.error(f"EVENT_PUBLISH_FAILED: job {case_id} marked COMPLETED but 'Work Order Completed' not published")
        raise _HttpError(502, "EVENT_PUBLISH_FAILED", "The visit was recorded but the case could not be notified")

    result = {"jobId": case_id, "workOrderId": work_order_id, "jobStatus": "COMPLETED",
              "completedAt": completed_at, "eventId": event_id}
    _store_idempotent(idem_key, result)
    return result


# --- dispatch ------------------------------------------------------------------------------

_ROUTES: dict[str, Callable[[dict, str], dict]] = {
    "POST /mobile-web-app/jobs/{jobId}/status": _status,
    "POST /mobile-web-app/jobs/{jobId}/parts/used": _parts_used,
    "POST /mobile-web-app/jobs/{jobId}/parts/requests": _parts_requests,
    "POST /mobile-web-app/jobs/{jobId}/complete": _complete,
}


def handler(event: dict, context: Any = None) -> dict:
    route_key = event.get("routeKey", "")
    route = _ROUTES.get(route_key)
    if route is None:
        return c.error(404, "NOT_FOUND", f"Unknown route {route_key}")

    try:
        technician_id, _username = c.technician_identity(event)
    except c.AuthError as exc:
        return c.error(exc.status, exc.code, exc.message)

    try:
        body = route(event, technician_id)
    except _HttpError as exc:
        return c.error(exc.status, exc.code, exc.message)
    except CaseLockHeld as exc:
        return c.error(409, "CONFLICT", str(exc))
    except CaseNotFound as exc:
        return c.error(404, "NOT_FOUND", str(exc))
    except ConcurrentModification as exc:
        return c.error(409, "CONFLICT", str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.error(f"field_actions {route_key} failed: {exc}")
        return c.error(500, "INTERNAL_ERROR", "Internal error")
    return c.ok(body)
