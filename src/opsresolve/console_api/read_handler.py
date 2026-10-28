"""T4: read-only Ops Console API.

Handler: opsresolve.console_api.read_handler.handler   (HTTP API payload v2; dispatch on routeKey)

Routes (all GET, all behind the Cognito JWT authorizer):
  /cases?column=&limit=&recent=   /cases/summary   /cases/names   /cases/{id}
  /timeline?limit=                /cases/{id}/timeline            /cases/{id}/trace
  /approvals?status=PENDING|DECIDED                               /kpis?weeks=
  /tenant

Every route is scoped to the caller's tenant (JWT claim ``custom:tenantId``; a missing claim is a 403). A case
without a ``tenantId`` belongs to ``meravix-foods``. Another tenant's case reads as 404.

Read-only: this module never writes. Everything is derived from the case store
(META / EVT# / MSG# / APPT# / PLAN# items), the approvals table and mock-customers.
``GET /cases/{id}`` also joins mock-contacts / -sites / -assets / -contracts / -technicians
(GetItem or Scan, read-only) for its customer / asset / entitlement / technician blocks; a
failed or empty join degrades to null, never a 500.
It NEVER reads live telemetry (the app is archived between demos).

Case-store layout follows ``common/case_store.py``: pk=CASE#<id>; sk=META | EVT#<ts>#<id> |
MSG#<ts>#<id> | PLAN#v<n> | APPT#<wo>; GSI1 ``gsi1pk=STATE#<state>`` is the status board.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from functools import lru_cache
from typing import Any, Callable

from ..common import tenants
from ..common.logging import get_logger, mask_payload
from . import _common as c

logger = get_logger(__name__)

GSI1 = "GSI1"  # status-board index (resource.md); gsi1pk = STATE#<state>
GSI2 = "GSI2"  # asset-history index (resource.md); gsi2pk = ASSET#<assetId>
DEFAULT_LIMIT, MAX_LIMIT = 50, 100
DEFAULT_TIMELINE_LIMIT, MAX_TIMELINE_LIMIT = 50, 200
DEFAULT_WEEKS, MAX_WEEKS = 8, 52
DEFAULT_RECENT, MAX_RECENT = 5, 20
_CASE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TERMINAL = {"CLOSED", "CANCELLED"}
_ORCHESTRATOR = "OrchestratorAgent"


_TENANT = ""  # caller's tenant for the current invocation (set in handler)


class _HttpError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _bad(message: str) -> _HttpError:
    return _HttpError(400, "BAD_REQUEST", message)


def _not_found(message: str) -> _HttpError:
    return _HttpError(404, "NOT_FOUND", message)


def _case_tenant(meta: dict) -> str:
    return meta.get("tenantId") or tenants.DEFAULT_TENANT


# --- AWS access ----------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _ddb():  # pragma: no cover - thin SDK wrapper
    import boto3

    return boto3.resource("dynamodb", region_name=c.settings.region)


def _table(name: str):
    return _ddb().Table(name)


def _paginate(fn: Callable[..., dict], **kwargs: Any) -> list[dict]:
    """Run a query/scan to exhaustion (LastEvaluatedKey loop). Never pass Limit: it applies
    before FilterExpression, so a limited scan can return a short/empty page."""
    items: list[dict] = []
    while True:
        resp = fn(**kwargs)
        items.extend(resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            return items
        kwargs["ExclusiveStartKey"] = lek


def _count(fn: Callable[..., dict], **kwargs: Any) -> int:
    total = 0
    while True:
        resp = fn(Select="COUNT", **kwargs)
        total += resp.get("Count", 0)
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            return total
        kwargs["ExclusiveStartKey"] = lek


def _query_case_partition(case_id: str) -> list[dict]:
    from boto3.dynamodb.conditions import Key

    t = _table(c.CASES_TABLE)
    items = _paginate(t.query, KeyConditionExpression=Key("pk").eq(f"CASE#{case_id}"))
    return items + _conversation_msgs(case_id, {str(i.get("messageId")) for i in items if i.get("messageId")})


def _is_customer_msg(item: dict) -> bool:
    """Conversations-table rows the Gateway Lambda / inbound normalizer write (RCS, WhatsApp). Technician
    traffic (job alerts, Accept/Decline) is not part of the customer conversation."""
    return (str(item.get("sk", "")).startswith(c.MSG_SK_PREFIX)
            and "TECHNICIAN" not in (item.get("recipientType"), item.get("senderType")))


def _conversation_msgs(case_id: str, seen_ids: set[str]) -> list[dict]:
    """MSG# rows for the case from the conversations table (read-only), minus ones already in the cases table.
    Any SDK error -> warning + [] (the case itself must still load)."""
    from boto3.dynamodb.conditions import Key
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        rows = _paginate(_table(c.CONVERSATIONS_TABLE).query, KeyConditionExpression=Key("pk").eq(f"CASE#{case_id}"))
    except (ClientError, BotoCoreError) as exc:
        logger.warning("conversations read failed case=%s error=%s", case_id, type(exc).__name__)
        return []
    return [r for r in rows if _is_customer_msg(r) and str(r.get("messageId")) not in seen_ids]


def _metas_for_states(states: list[str]) -> list[dict]:
    """META items for the given states, via GSI1 (one paginated query per state)."""
    from boto3.dynamodb.conditions import Key

    t = _table(c.CASES_TABLE)
    out: list[dict] = []
    for s in states:
        out.extend(_paginate(t.query, IndexName=GSI1, KeyConditionExpression=Key("gsi1pk").eq(f"STATE#{s}")))
    return [m for m in out if m.get("sk") == "META" and _case_tenant(m) == _TENANT]


# --- per-invocation caches -------------------------------------------------------------------
_customers: dict[str, dict | None] = {}
_partitions: dict[str, list[dict]] = {}
_mock_items: dict[tuple[str, str], dict | None] = {}
_mock_scans: dict[tuple[str, str, str], list[dict]] = {}
_mock_scan_failed: set[tuple[str, str, str]] = set()  # scans that raised (result is [] but unknown)


def _reset_caches() -> None:
    _customers.clear()
    _partitions.clear()
    _mock_items.clear()
    _mock_scans.clear()
    _mock_scan_failed.clear()


def _mock_get(table: str, pk: str) -> dict | None:
    """GetItem pk/sk=META on a mock table; any SDK error -> warning + None (never a 500)."""
    from botocore.exceptions import BotoCoreError, ClientError

    key = (table, pk)
    if key not in _mock_items:
        try:
            _mock_items[key] = _table(table).get_item(Key={"pk": pk, "sk": "META"}).get("Item")
        except (ClientError, BotoCoreError) as exc:
            logger.warning("mock join failed table=%s pk=%s error=%s", table, pk, type(exc).__name__)
            _mock_items[key] = None
    return _mock_items[key]


def _mock_scan(table: str, attr: str, value: str) -> list[dict]:
    """Scan a mock table filtered on attr == value; any SDK error -> warning + []."""
    from boto3.dynamodb.conditions import Attr
    from botocore.exceptions import BotoCoreError, ClientError

    key = (table, attr, value)
    if key not in _mock_scans:
        try:
            _mock_scans[key] = _paginate(_table(table).scan, FilterExpression=Attr(attr).eq(value))
        except (ClientError, BotoCoreError) as exc:
            logger.warning("mock scan failed table=%s %s=%s error=%s", table, attr, value, type(exc).__name__)
            _mock_scans[key] = []
            _mock_scan_failed.add(key)
    return _mock_scans[key]


def _partition(case_id: str) -> list[dict]:
    if case_id not in _partitions:
        _partitions[case_id] = _query_case_partition(case_id)
    return _partitions[case_id]


def _customer(customer_id: str | None) -> dict | None:
    if not customer_id:
        return None
    if customer_id not in _customers:
        resp = _table(c.settings.mock_customers_table).get_item(
            Key={"pk": f"CUSTOMERS#{customer_id}", "sk": "META"}
        )
        _customers[customer_id] = resp.get("Item")
    return _customers[customer_id]


# --- helpers -------------------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _int_param(event: dict, name: str, default: int, lo: int, hi: int) -> int:
    """Query-string int: absent -> default; non-numeric -> 400; out of range -> clamped."""
    raw = (event.get("queryStringParameters") or {}).get(name)
    if raw is None or raw == "":
        return default
    try:
        val = int(raw)
    except (TypeError, ValueError):
        raise _bad(f"'{name}' must be an integer") from None
    return max(lo, min(hi, val))


def _case_id(event: dict) -> str:
    cid = (event.get("pathParameters") or {}).get("id") or ""
    if not _CASE_ID_RE.match(cid):
        raise _bad("invalid case id")
    return cid


def _meta_or_404(case_id: str) -> dict:
    for it in _partition(case_id):
        if it.get("sk") == "META" and _case_tenant(it) == _TENANT:
            return it
    raise _not_found(f"case {case_id} not found")


def _by_prefix(items: list[dict], prefix: str) -> list[dict]:
    return sorted((i for i in items if str(i.get("sk", "")).startswith(prefix)), key=lambda i: i["sk"])


def _event_view(item: dict) -> dict:
    _, ts, eid = (item["sk"].split("#", 2) + ["", ""])[:3]
    return {
        "eventId": eid,
        "caseId": str(item.get("pk", "")).removeprefix("CASE#"),
        "ts": ts,
        "type": item.get("type"),
        "actor": item.get("actor"),
        "summary": mask_payload(item.get("summary"), max_str=2000),
        "toolName": item.get("toolName"),
        "policyDecision": item.get("policyDecision"),
        "correlationId": item.get("correlationId"),
        "detail": mask_payload(item.get("detail") or {}, max_str=2000),
    }


def _title(events: list[dict], meta: dict) -> str:
    """Title = META.title when set, else the CASE_OPENED trigger. Trigger is {"type","name","eventId"}
    (orchestrator classify_trigger); prefer an explicit title/summary if the seed/producer provided one."""
    if meta.get("title"):
        return str(meta["title"])
    for e in events:
        if e.get("type") == "CASE_OPENED":
            trig = (e.get("detail") or {}).get("trigger") or {}
            t = trig.get("title") or trig.get("summary") or trig.get("name") or trig.get("type")
            if t:
                return str(t)
    return f"Case {meta.get('caseId')}"


def _card(meta: dict, items: list[dict]) -> dict:
    """Board card = META + read-time enrichment (customer name, title, channel, SLA)."""
    events = _by_prefix(items, "EVT#")
    msgs = _by_prefix(items, c.MSG_SK_PREFIX)
    cust = _customer(meta.get("customerId")) or {}
    state = meta.get("state")
    restore_by = meta.get("restoreBy")
    sla = None
    rb = _parse_ts(restore_by)
    if rb:
        remaining = int((rb - _now()).total_seconds())
        sla = {
            "restoreBy": restore_by,
            "secondsRemaining": remaining,
            "breached": remaining < 0 and state not in _TERMINAL,
        }
    return {
        "caseId": meta.get("caseId"),
        "state": state,
        "column": c.STATE_TO_COLUMN.get(state),
        "priority": meta.get("priority"),
        "customerId": meta.get("customerId"),
        "customerName": cust.get("name"),
        "assetId": meta.get("assetId"),
        "title": _title(events, meta),
        "channel": msgs[-1].get("channel") if msgs else None,
        "createdAt": meta.get("createdAt"),
        "closedAt": meta.get("closedAt"),
        "restoreBy": restore_by,
        "sla": sla,
        "version": meta.get("version"),
    }


def _all_cards(states: list[str] | None = None) -> list[dict]:
    metas = _metas_for_states(states or list(c.STATE_TO_COLUMN))
    metas.sort(key=lambda m: (m.get("createdAt") or "", m.get("caseId") or ""), reverse=True)
    return [_card(m, _partition(m["caseId"])) for m in metas]


# --- routes --------------------------------------------------------------------------------
def _list_cases(event: dict) -> dict:
    qs = event.get("queryStringParameters") or {}
    if qs.get("recent") not in (None, ""):
        n = _int_param(event, "recent", DEFAULT_RECENT, 1, MAX_RECENT)
        return {"cases": _all_cards()[:n]}
    column = qs.get("column")
    if column not in (None, "") and column not in c.COLUMN_STATES:
        raise _bad(f"'column' must be one of {list(c.COLUMN_STATES)}")
    limit = _int_param(event, "limit", DEFAULT_LIMIT, 1, MAX_LIMIT)
    states = c.COLUMN_STATES[column] if column else None
    cards = _all_cards(states)
    return {"cases": cards[:limit], "count": min(len(cards), limit), "total": len(cards)}


def _summary(event: dict) -> dict:
    columns = {col: len(_metas_for_states(states)) for col, states in c.COLUMN_STATES.items()}
    return {"columns": columns, "total": sum(columns.values())}


def _names(event: dict) -> dict:
    return {
        "cases": [
            {"caseId": k["caseId"], "customerName": k["customerName"], "assetId": k["assetId"], "title": k["title"]}
            for k in _all_cards()
        ]
    }


_CASE_DETAIL_KEYS = ("category", "subcategory", "subcategoryTag", "assignedQueue", "caseOwner")
_TRIGGER_SOURCE = {"TELEMETRY": "IOT_TELEMETRY"}


def _case_extras(meta: dict, events: list[dict]) -> dict:
    """Case Information keys (case detail only). sourceChannel falls back to the CASE_OPENED
    trigger for agent-opened cases: its ``channel`` when set, else TELEMETRY -> IOT_TELEMETRY."""
    source = meta.get("sourceChannel")
    if not source:
        for e in events:
            if e.get("type") == "CASE_OPENED":
                trig = (e.get("detail") or {}).get("trigger") or {}
                source = trig.get("channel") or _TRIGGER_SOURCE.get(str(trig.get("type")))
                break
    desc = meta.get("customerDescription")
    return {
        **{k: meta.get(k) for k in _CASE_DETAIL_KEYS},
        "customerDescription": mask_payload(desc, max_str=2000) if desc else None,
        "sourceChannel": source or None,
    }


_CREDIT_EVENTS = {
    "SERVICE_CREDIT_APPLIED": "APPLIED",
    "SERVICE_CREDIT_PENDING_APPROVAL": "PENDING_APPROVAL",
    "SERVICE_CREDIT_NOT_ELIGIBLE": "NOT_ELIGIBLE",
    "SERVICE_CREDIT_FAILED": "FAILED",
}


def _service_credit(items: list[dict]) -> dict | None:
    """Latest service-credit outcome from the timeline (none for tenants without a credit policy)."""
    for e in reversed(_by_prefix(items, "EVT#")):
        status = _CREDIT_EVENTS.get(str(e.get("type")))
        if status:
            d = e.get("detail") or {}
            return {"status": status, "amountUsd": d.get("amountUsd"), "downtimeHours": d.get("downtimeHours"),
                    "creditId": d.get("creditId"), "approvalId": d.get("approvalId")}
    return None


def _date(value: Any) -> str | None:
    """Calendar date YYYY-MM-DD from a date or ISO timestamp string."""
    return value[:10] if isinstance(value, str) and len(value) >= 10 else None


def _site_ref(site_id: Any) -> dict | None:
    if not site_id:
        return None
    return _mock_get(c.settings.mock_sites_table, f"SITES#{site_id}")


def _customer_view(meta: dict, asset: dict | None) -> dict | None:
    cust = _customer(meta.get("customerId"))
    if not cust:
        return None
    contacts = sorted(_mock_scan(c.settings.mock_contacts_table, "customerId", cust.get("customerId") or meta["customerId"]),
                      key=lambda x: str(x.get("contactId") or ""))
    primary = next((x for x in contacts if x.get("isPrimary") is True), contacts[0] if contacts else None)
    site_id = (asset or {}).get("siteId")
    site = _site_ref(site_id)
    return {
        "customerId": cust.get("customerId") or meta.get("customerId"),
        "name": cust.get("name"),
        "segment": cust.get("segment"),
        "primaryContact": None if primary is None else {
            "contactId": primary.get("contactId"),
            "name": primary.get("name"),
            "role": primary.get("role"),
            "email": primary.get("email"),
            "phone": primary.get("phone"),
        },
        "preferredChannel": (primary or {}).get("preferredChannel"),
        "serviceLocation": None if site is None else {
            "siteId": site.get("siteId") or site_id,
            "name": site.get("name"),
            "address": site.get("address"),
            "accessNotes": site.get("accessNotes"),
        },
    }


def _last_service(asset: dict, case_id: str) -> str | None:
    """max(asset.lastServiceDate, closedAt date of OTHER closed cases on the asset via GSI2)."""
    from boto3.dynamodb.conditions import Key
    from botocore.exceptions import BotoCoreError, ClientError

    dates = [d for d in [_date(asset.get("lastServiceDate"))] if d]
    try:
        others = _paginate(_table(c.CASES_TABLE).query, IndexName=GSI2,
                           KeyConditionExpression=Key("gsi2pk").eq(f"ASSET#{asset.get('assetId')}"))
    except (ClientError, BotoCoreError) as exc:
        logger.warning("asset history query failed asset=%s error=%s", asset.get("assetId"), type(exc).__name__)
        others = []
    for m in others:
        if m.get("sk") == "META" and m.get("state") == "CLOSED" and m.get("caseId") != case_id:
            d = _date(m.get("closedAt"))
            if d:
                dates.append(d)
    return max(dates) if dates else None


def _asset_view(meta: dict, asset: dict | None) -> dict | None:
    if not asset:
        return None
    site = _site_ref(asset.get("siteId"))
    return {
        "assetId": asset.get("assetId") or meta.get("assetId"),
        "model": asset.get("model"),
        "displayName": asset.get("displayName"),
        "serialNumber": asset.get("serial"),
        "installedAt": _date(asset.get("installDate")),
        "criticality": asset.get("criticality"),
        "site": None if not asset.get("siteId") else {
            "siteId": asset.get("siteId"),
            "name": (site or {}).get("name"),
        },
        "lastServiceAt": _last_service(asset, meta.get("caseId")),
    }


def _entitlement_view(meta: dict) -> dict:
    """Always an object. coverageStatus: ACTIVE | EXPIRED (expiresAt before today, UTC) |
    NOT_COVERED (lookup worked, no contract for this customer/asset; every other field null) |
    UNKNOWN (the contracts lookup raised, so coverage can't be stated; every other field null)."""
    cust_id, asset_id = meta.get("customerId"), meta.get("assetId")
    contracts = sorted(_mock_scan(c.settings.mock_contracts_table, "customerId", cust_id) if cust_id else [],
                       key=lambda x: str(x.get("contractId") or ""))
    contract = next((k for k in contracts if asset_id in (k.get("assetIds") or [])), None) \
        or next((k for k in contracts if not k.get("assetIds")), None)
    if contract is None:
        lookup_failed = bool(cust_id) and (c.settings.mock_contracts_table, "customerId", cust_id) in _mock_scan_failed
        return {"contractId": None, "coverage": None, "slaTier": None, "restoreHours": None,
                "coverageStatus": "UNKNOWN" if lookup_failed else "NOT_COVERED",
                "partsCovered": None, "laborCovered": None, "expiresAt": None}
    cov = contract.get("coverage") or {}
    expires = _date(contract.get("endDate") or contract.get("warrantyUntil"))
    expired = bool(expires) and expires < _now().strftime("%Y-%m-%d")
    return {
        "contractId": contract.get("contractId"),
        "coverage": contract.get("planName") or contract.get("slaTier"),
        "slaTier": contract.get("slaTier"),
        "restoreHours": contract.get("restoreHours"),
        "coverageStatus": "EXPIRED" if expired else "ACTIVE",
        "partsCovered": bool(cov.get("parts") or cov.get("hardware")),
        "laborCovered": bool(cov.get("labour") or cov.get("labor")),
        "expiresAt": expires,
    }


def _appointment_view(a: dict) -> dict:
    view = mask_payload({k: v for k, v in a.items() if k not in ("pk", "sk")}, max_str=2000) \
        | {"workOrderId": a["sk"].removeprefix("APPT#")}
    if "signatureKey" in a:  # an S3 object key, not a secret; mask_payload's "signature" rule would hide it
        view["signatureKey"] = a["signatureKey"]
    tech_id = a.get("technicianId")
    tech = _mock_get(c.settings.mock_technicians_table, f"TECHNICIANS#{tech_id}") if tech_id else None
    start, end = _parse_ts(a.get("slotStart")), _parse_ts(a.get("slotEnd"))
    minutes = int((end - start).total_seconds() // 60) if start and end and end > start else None
    return view | {"technicianName": (tech or {}).get("name"), "estimatedDurationMinutes": minutes}


UPLOADS_BUCKET = f"{c.settings.prefix}-field-uploads"  # written by the mobile web app (opsresolve-dev-field-uploads)
ATTACHMENT_URL_TTL = 900  # seconds a presigned preview/download URL stays valid


@lru_cache(maxsize=1)
def _s3():  # pragma: no cover - thin SDK wrapper
    import boto3
    from botocore.config import Config

    return boto3.client("s3", region_name=c.settings.region, config=Config(signature_version="s3v4"))


def _attachments(case_id: str, appts: list[dict]) -> list[dict]:
    """Technician evidence for the case: the photoKeys / signatureKey the mobile app saved on each APPT# item
    when the visit was completed, with a presigned preview URL (inline) and download URL (attachment).

    An object that no longer exists (bucket lifecycle deletes after 30 days) or cannot be read is skipped with
    a warning; the case itself must still load. Keys outside ``field/<caseId>/`` are ignored.
    """
    from botocore.exceptions import BotoCoreError, ClientError

    out: list[dict] = []
    seen: set[str] = set()
    for a in appts:
        keys = [k for k in (a.get("photoKeys") or []) if isinstance(k, str)]
        if isinstance(a.get("signatureKey"), str):
            keys.append(a["signatureKey"])
        for key in keys:
            if key in seen or not key.startswith(f"field/{case_id}/"):
                continue
            seen.add(key)
            try:
                head = _s3().head_object(Bucket=UPLOADS_BUCKET, Key=key)
                name = key.rsplit("/", 1)[-1]
                params = {"Bucket": UPLOADS_BUCKET, "Key": key}
                preview = _s3().generate_presigned_url("get_object", Params=params, ExpiresIn=ATTACHMENT_URL_TTL)
                download = _s3().generate_presigned_url(
                    "get_object", ExpiresIn=ATTACHMENT_URL_TTL,
                    Params=params | {"ResponseContentDisposition": f'attachment; filename="{name}"'})
            except (ClientError, BotoCoreError) as exc:
                logger.warning(f"attachment unavailable key={key} err={type(exc).__name__}")
                continue
            parts = key.split("/")
            out.append({
                "attachmentId": name.rsplit(".", 1)[0],
                "fileName": name,
                "kind": parts[2] if len(parts) > 3 else None,  # PHOTO | SIGNATURE
                "contentType": head.get("ContentType"),
                "sizeBytes": head.get("ContentLength"),
                "uploadedAt": _iso_utc(head.get("LastModified")),
                "uploadedBy": a.get("technicianId"),
                "workOrderId": a["sk"].removeprefix("APPT#"),
                "previewUrl": preview,
                "downloadUrl": download,
                "urlExpiresInSeconds": ATTACHMENT_URL_TTL,
            })
    out.sort(key=lambda x: (x["uploadedAt"] or "", x["fileName"]), reverse=True)
    return out


def _iso_utc(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _get_case(event: dict) -> dict:
    cid = _case_id(event)
    meta = _meta_or_404(cid)
    items = _partition(cid)
    from boto3.dynamodb.conditions import Attr

    approvals = _paginate(_table(c.APPROVALS_TABLE).scan, FilterExpression=Attr("caseId").eq(cid))
    approvals.sort(key=lambda a: a.get("createdAt") or "")
    asset = _mock_get(c.settings.mock_assets_table, f"ASSETS#{meta['assetId']}") if meta.get("assetId") else None
    return {
        "case": _card(meta, items) | _case_extras(meta, _by_prefix(items, "EVT#")),
        "correlationId": meta.get("correlationId"),
        "plans": [
            mask_payload({k: v for k, v in p.items() if k not in ("pk", "sk")}, max_str=2000)
            | {"version": p["sk"].removeprefix("PLAN#")}
            for p in _by_prefix(items, "PLAN#")
        ],
        "appointments": [_appointment_view(a) for a in _by_prefix(items, "APPT#")],
        "messages": [
            mask_payload({k: m.get(k) for k in c.MSG_ATTRS}, max_str=2000) for m in _by_prefix(items, c.MSG_SK_PREFIX)
        ],
        "approvals": [{k: a.get(k) for k in c.APPROVAL_ATTRS} for a in approvals],
        "customer": _customer_view(meta, asset),
        "asset": _asset_view(meta, asset),
        "entitlement": _entitlement_view(meta),
        "requiredPart": meta.get("requiredPart") or None,
        "resolutionPath": meta.get("resolutionPath") or None,
        "serviceCredit": _service_credit(items),
        "attachments": _attachments(cid, _by_prefix(items, "APPT#")),
    }


def _global_timeline(event: dict) -> dict:
    from boto3.dynamodb.conditions import Attr

    limit = _int_param(event, "limit", DEFAULT_TIMELINE_LIMIT, 1, MAX_TIMELINE_LIMIT)
    mine = {m["caseId"] for m in _metas_for_states(list(c.STATE_TO_COLUMN))}
    items = _paginate(_table(c.CASES_TABLE).scan, FilterExpression=Attr("sk").begins_with("EVT#"))
    items = [i for i in items if str(i.get("pk", "")).removeprefix("CASE#") in mine]
    events = sorted((_event_view(i) for i in items), key=lambda e: (e["ts"], e["eventId"]), reverse=True)
    return {"events": events[:limit], "count": min(len(events), limit)}


def _case_timeline(event: dict) -> dict:
    cid = _case_id(event)
    _meta_or_404(cid)
    limit = _int_param(event, "limit", MAX_TIMELINE_LIMIT, 1, MAX_TIMELINE_LIMIT * 2)
    events = [_event_view(i) for i in _by_prefix(_partition(cid), "EVT#")]
    newest_first = events[-limit:][::-1]  # the newest `limit` events, latest first
    return {"caseId": cid, "events": newest_first, "count": len(newest_first)}


def _span(ev: dict, kind: str, name: str, parent: str | None) -> dict:
    d = ev["detail"]
    return {
        "spanId": ev["eventId"],
        "parentId": parent,
        "kind": kind,
        "name": name,
        "actor": ev["actor"],
        "startedAt": ev["ts"],
        "correlationId": ev["correlationId"],
        "latencyMs": d.get("latencyMs"),
        "status": d.get("status"),
        "input": d.get("input"),
        "output": d.get("output"),
        "policyDecision": ev["policyDecision"],
        "children": [],
    }


def _trace(event: dict) -> dict:
    """Span tree from timeline events (NOT X-Ray/CloudWatch; seeded cases have no real spans).

    case -> ORCHESTRATOR (one per correlationId = one control-loop run) -> SPECIALIST
    (one per SPECIALIST_DELEGATED event, named detail.specialist) -> TOOL (one per entry of
    detail.toolCalls = the calls the specialist really made, with input/output/latencyMs/status;
    events without toolCalls, i.e. older runs and seeded cases, get one span per name in
    detail.expectedTools that inherits the specialist's status with latencyMs/input/output =
    null). Any other event carrying
    detail.latencyMs is a TOOL span under the specialist named by detail.specialist / an AGENT
    actor name when that span exists, else under the orchestrator. Remaining events become
    ``markers`` on the orchestrator span. Orchestrator latencyMs = wall-clock first->last event
    (timestamps have 1 s resolution); its status is ERROR if any descendant is ERROR.
    """
    cid = _case_id(event)
    _meta_or_404(cid)
    events = [_event_view(i) for i in _by_prefix(_partition(cid), "EVT#")]
    groups: dict[str, list[dict]] = {}
    for e in events:
        groups.setdefault(e["correlationId"] or "uncorrelated", []).append(e)

    orchestrators: list[dict] = []
    count = 0
    for corr, evs in groups.items():
        first = evs[0]
        root = _span(first, "ORCHESTRATOR", _ORCHESTRATOR, f"case:{cid}")
        root.update(spanId=f"orch:{corr}", correlationId=corr, latencyMs=None, status=None,
                    input=None, output=None, policyDecision=None, markers=[])
        specialists: dict[str, dict] = {}
        for e in evs:
            d = e["detail"]
            if e["type"] == "SPECIALIST_DELEGATED":
                name = d.get("specialist") or (e["actor"] or {}).get("name") or "Specialist"
                sp = _span(e, "SPECIALIST", name, root["spanId"])
                recorded = d.get("toolCalls")
                if isinstance(recorded, list):
                    # Live runs: the tool calls the specialist really made, each with its own
                    # input / output / latency / status (zero calls is a valid, visible outcome).
                    for i, call in enumerate(x for x in recorded if isinstance(x, dict)):
                        ts_ = _span(e, "TOOL", str(call.get("tool") or "tool"), sp["spanId"])
                        ts_.update(spanId=f"{e['eventId']}:{i}", latencyMs=call.get("latencyMs"),
                                   status=call.get("status"), input=call.get("input"), output=call.get("output"))
                        sp["children"].append(ts_)
                else:
                    # Older events carry only the specialist's total: one span per expected tool.
                    tools = d.get("expectedTools") or [t.strip() for t in (e["toolName"] or "").split(",") if t.strip()]
                    for i, tool in enumerate(tools):
                        ts_ = _span(e, "TOOL", tool, sp["spanId"])
                        ts_.update(spanId=f"{e['eventId']}:{i}", latencyMs=None, status=d.get("status"),
                                   input=None, output=None)
                        sp["children"].append(ts_)
                specialists[name] = sp
                root["children"].append(sp)
                count += 1 + len(sp["children"])
            elif d.get("latencyMs") is not None:
                owner = d.get("specialist") or (e["actor"] or {}).get("name")
                parent = specialists.get(owner, root)
                parent["children"].append(_span(e, "TOOL", e["toolName"] or e["type"], parent["spanId"]))
                count += 1
            else:
                root["markers"].append({"ts": e["ts"], "type": e["type"], "summary": e["summary"]})
        # Orchestrator span: input = the trigger recorded when the case was opened / the event merged
        # into it (other triggers are not stored on the timeline), output = the last state change.
        trigger = next((t for t in ((x["detail"] or {}).get("trigger") for x in evs) if t), None)
        changed = next((x for x in reversed(evs) if x["type"] == "CASE_STATE_CHANGED"), None)
        root["input"] = {"trigger": trigger} if trigger else None
        root["output"] = {k: changed["detail"].get(k) for k in ("from", "to", "reason")} if changed else None
        statuses = [s["status"] for sp in root["children"] for s in [sp, *sp["children"]]]
        root["status"] = "ERROR" if "ERROR" in statuses else ("OK" if statuses else None)
        t0, t1 = _parse_ts(evs[0]["ts"]), _parse_ts(evs[-1]["ts"])
        root["latencyMs"] = int((t1 - t0).total_seconds() * 1000) if t0 and t1 else None
        orchestrators.append(root)
        count += 1

    return {
        "caseId": cid,
        "spanCount": count + 1,
        "trace": {
            "spanId": f"case:{cid}", "parentId": None, "kind": "CASE", "name": cid,
            "children": orchestrators,
        },
    }


def _approvals(event: dict) -> dict:
    from boto3.dynamodb.conditions import Attr

    status = (event.get("queryStringParameters") or {}).get("status")
    if status in (None, ""):
        flt = None
    elif status == "PENDING":
        flt = Attr("status").eq(c.APPROVAL_STATUS_PENDING)
    elif status == "DECIDED":
        flt = Attr("status").is_in(list(c.APPROVAL_DECIDED_STATUSES))
    else:
        raise _bad("'status' must be PENDING or DECIDED")
    kw = {"FilterExpression": flt} if flt is not None else {}
    rows = _paginate(_table(c.APPROVALS_TABLE).scan, **kw)
    rows.sort(key=lambda a: a.get("createdAt") or "", reverse=True)
    out = []
    for a in rows:
        view = {k: a.get(k) for k in c.APPROVAL_ATTRS}
        cid = a.get("caseId")
        meta = next((i for i in _partition(cid) if i.get("sk") == "META"), None) if cid else None
        if meta and _case_tenant(meta) != _TENANT:
            continue
        if meta:
            card = _card(meta, _partition(cid))
            view.update(caseTitle=card["title"], customerId=card["customerId"],
                        customerName=card["customerName"], assetId=card["assetId"], caseState=card["state"],
                        casePriority=card["priority"], sla=card["sla"])
        elif tenants.DEFAULT_TENANT == _TENANT:  # an approval whose case is gone belongs to the default tenant
            view.update(caseTitle=None, customerId=None, customerName=None, assetId=None, caseState=None,
                        casePriority=None, sla=None)
        else:
            continue
        out.append(view)
    return {"approvals": out, "count": len(out)}


def _week_start(dt: datetime) -> datetime:
    d = dt.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return d - timedelta(days=d.weekday())  # Monday 00:00 UTC


def _kpis(event: dict) -> dict:
    """KPIs, all from case META / timeline events / MSG# / APPT# items (never telemetry).

    Window = the last ``weeks`` ISO weeks (Mon 00:00 UTC buckets) ending with the current week;
    a case/event belongs to the window by the timestamp named in each definition. Anchored on
    "now", so seed timestamps must be relative to seed time to show data.

    - mttr.minutes: mean of (closedAt - createdAt) in minutes over cases with state CLOSED
      (CANCELLED excluded) and a closedAt inside the window; null if none.
      mttr.trend[]: same metric per week bucket by closedAt {weekStart, mttrMinutes, casesClosed}.
    - slaBreachesAvoided.total: count of SLA_BREACH_AVOIDED timeline events whose timestamp
      (the sk ts) is in the window; .trend[] = per-week counts {weekStart, count}.
    - remoteResolutionRate.rate: resolved cases = createdAt in window AND state in
      (RESOLVED_REMOTE, CLOSED); remote = state RESOLVED_REMOTE OR no APPT# item (no field
      dispatch). rate = remote / resolved (0..1, 3 dp), null if no resolved cases.
    - csat.average: mean of detail.score (1..5, 1 dp) over CSAT_RECEIVED events in the window;
      .responses = count; null average if none.
    - casesByChannel: count of cases created in the window keyed by channel of the latest MSG#
      item; cases with no messages are counted under "UNKNOWN".
    """
    weeks = _int_param(event, "weeks", DEFAULT_WEEKS, 1, MAX_WEEKS)
    now = _now()
    first_week = _week_start(now) - timedelta(weeks=weeks - 1)
    starts = [first_week + timedelta(weeks=i) for i in range(weeks)]

    def bucket(dt: datetime | None) -> int | None:
        if dt is None or dt < first_week or dt >= first_week + timedelta(weeks=weeks):
            return None
        return (dt - first_week).days // 7

    from boto3.dynamodb.conditions import Attr

    items = _paginate(_table(c.CASES_TABLE).scan, FilterExpression=~Attr("sk").begins_with("PLAN#"))
    try:  # WhatsApp / RCS messages live in the conversations table
        from botocore.exceptions import BotoCoreError, ClientError

        items += [r for r in _paginate(_table(c.CONVERSATIONS_TABLE).scan,
                                       FilterExpression=Attr("sk").begins_with(c.MSG_SK_PREFIX))
                  if _is_customer_msg(r)]
    except (ClientError, BotoCoreError) as exc:
        logger.warning("conversations scan failed error=%s", type(exc).__name__)
    mine = {str(i.get("pk", "")).removeprefix("CASE#") for i in items
            if i.get("sk") == "META" and _case_tenant(i) == _TENANT}
    items = [i for i in items if str(i.get("pk", "")).removeprefix("CASE#") in mine]
    metas: dict[str, dict] = {}
    appt_cases: set[str] = set()
    latest_msg: dict[str, tuple[str, str]] = {}
    csat: list[int] = []
    avoided = [0] * weeks
    for it in items:
        sk, pk = str(it.get("sk", "")), str(it.get("pk", ""))
        cid = pk.removeprefix("CASE#")
        if sk == "META":
            metas[cid] = it
        elif sk.startswith("APPT#"):
            appt_cases.add(cid)
        elif sk.startswith(c.MSG_SK_PREFIX):
            if cid not in latest_msg or sk > latest_msg[cid][0]:
                latest_msg[cid] = (sk, it.get("channel") or "UNKNOWN")
        elif sk.startswith("EVT#"):
            ev = _event_view(it)
            b = bucket(_parse_ts(ev["ts"]))
            if b is None:
                continue
            if ev["type"] == c.EVT_CSAT:
                score = ev["detail"].get("score")
                if isinstance(score, (int, float, Decimal)) and 1 <= score <= 5:
                    csat.append(float(score))
            elif ev["type"] == c.EVT_SLA_BREACH_AVOIDED:
                avoided[b] += 1

    closed_minutes: list[list[float]] = [[] for _ in range(weeks)]
    resolved = remote = 0
    channels: dict[str, int] = {}
    for cid, m in metas.items():
        created = _parse_ts(m.get("createdAt"))
        closed = _parse_ts(m.get("closedAt"))
        if m.get("state") == "CLOSED" and created and closed:
            b = bucket(closed)
            if b is not None:
                closed_minutes[b].append((closed - created).total_seconds() / 60)
        if bucket(created) is not None:
            channels[latest_msg.get(cid, ("", "UNKNOWN"))[1]] = channels.get(latest_msg.get(cid, ("", "UNKNOWN"))[1], 0) + 1
            if m.get("state") in ("RESOLVED_REMOTE", "CLOSED"):
                resolved += 1
                if m.get("state") == "RESOLVED_REMOTE" or cid not in appt_cases:
                    remote += 1

    def mean(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 1) if xs else None

    all_closed = [x for w in closed_minutes for x in w]
    return {
        "weeks": weeks,
        "windowStart": _iso(first_week),
        "mttr": {
            "minutes": mean(all_closed),
            "casesClosed": len(all_closed),
            "trend": [
                {"weekStart": _iso(s), "mttrMinutes": mean(w), "casesClosed": len(w)}
                for s, w in zip(starts, closed_minutes)
            ],
        },
        "slaBreachesAvoided": {
            "total": sum(avoided),
            "trend": [{"weekStart": _iso(s), "count": n} for s, n in zip(starts, avoided)],
        },
        "remoteResolutionRate": {
            "rate": round(remote / resolved, 3) if resolved else None,
            "remoteResolved": remote,
            "resolved": resolved,
        },
        "csat": {"average": mean(csat), "responses": len(csat)},
        "casesByChannel": channels,
    }


def _get_tenant(event: dict) -> dict:
    try:
        t = tenants.tenant(_TENANT)
    except LookupError:
        raise _not_found(f"tenant {_TENANT} not found") from None
    return {
        "tenantId": t.get("tenantId") or _TENANT,
        "displayName": t.get("displayName"),
        "scenario": t.get("scenario"),
        "scenarioButtons": t.get("scenarioButtons") or [],
        "telemetryProfiles": t.get("telemetryProfiles") or [],
        "demoAssetId": t.get("demoAssetId"),
    }


ROUTES: dict[str, Callable[[dict], dict]] = {
    "GET /cases": _list_cases,
    "GET /cases/summary": _summary,
    "GET /cases/names": _names,
    "GET /cases/{id}": _get_case,
    "GET /timeline": _global_timeline,
    "GET /cases/{id}/timeline": _case_timeline,
    "GET /cases/{id}/trace": _trace,
    "GET /approvals": _approvals,
    "GET /kpis": _kpis,
    "GET /tenant": _get_tenant,
}


def handler(event: dict, context: Any = None) -> dict:
    route = event.get("routeKey")
    fn = ROUTES.get(route or "")
    if fn is None:
        return c.error(404, "NOT_FOUND", "route not found")
    global _TENANT
    _TENANT = c.tenant_id(event) or ""
    if not _TENANT:
        return c.error(403, "FORBIDDEN", c.NO_TENANT_MESSAGE)
    _reset_caches()
    tenants.reset_cache()
    try:
        return c.ok(fn(event))
    except _HttpError as exc:
        return c.error(exc.status, exc.code, exc.message)
    except Exception:  # noqa: BLE001 - never leak internals to the client
        logger.exception("read_handler failed route=%s", route)
        return c.error(500, "INTERNAL_ERROR", "internal error")
