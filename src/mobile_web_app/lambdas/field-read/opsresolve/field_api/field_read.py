"""Field app read-only Lambda: GET /mobile-web-app/{me, jobs, jobs/{id}, depot-stock, parts/{id}}.

Handler: opsresolve.field_api.field_read.handler  (HTTP API payload v2; dispatch on routeKey)

Routes (all behind the ``opsresolve-dev-field-cognito`` JWT authorizer, technician-only):
  GET /mobile-web-app/me
  GET /mobile-web-app/jobs?view=today|upcoming|completed
  GET /mobile-web-app/jobs/{jobId}
  GET /mobile-web-app/depot-stock?jobId=
  GET /mobile-web-app/parts/{partId}
  GET /mobile-web-app/availability?status=AVAILABLE|BOOKED (technician-availability-scheduling
      spec -- optional filter; omitted returns every slot regardless of status)

Read-only, same shape as console_api/read_handler.py: a failed/empty join degrades to null,
never a 500. See API_requirements.md Sections 1, 2, 3, 5, 8 for the exact response contracts
and the "Notes (missing data)" call-outs reproduced inline below.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Callable

from ..common.config import settings
from ..common.logging import get_logger, mask_payload
from . import _common as c

logger = get_logger(__name__)

_CSAT_EVT_TYPE = "CSAT_RECEIVED"


class _HttpError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _bad(message: str) -> _HttpError:
    return _HttpError(400, "BAD_REQUEST", message)


def _not_found(message: str) -> _HttpError:
    return _HttpError(404, "NOT_FOUND", message)


def _forbidden(message: str) -> _HttpError:
    return _HttpError(403, "FORBIDDEN", message)


@lru_cache(maxsize=1)
def _ddb():  # pragma: no cover - thin SDK wrapper
    import boto3

    return boto3.resource("dynamodb", region_name=settings.region)


def _table(name: str):
    return _ddb().Table(name)


def _paginate(fn: Callable[..., dict], **kwargs: Any) -> list[dict]:
    items: list[dict] = []
    while True:
        resp = fn(**kwargs)
        items.extend(resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            return items
        kwargs["ExclusiveStartKey"] = lek


def _mock_get(table: str, pk: str) -> dict | None:
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        return _table(table).get_item(Key={"pk": pk, "sk": "META"}).get("Item")
    except (ClientError, BotoCoreError) as exc:
        logger.warning("mock join failed table=%s pk=%s error=%s", table, pk, type(exc).__name__)
        return None


def _mock_get_part(part_id: str) -> dict | None:
    """mock-parts has drifted to a hash-only `partId` key (confirmed live, GAPS.md #2) --
    try that schema first, falling back to the documented pk/sk shape in case it gets
    reseeded back. Any lookup error degrades to None, never a 500."""
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        item = _table(settings.mock_parts_table).get_item(Key={"partId": part_id}).get("Item")
        if item:
            return item
    except (ClientError, BotoCoreError) as exc:
        logger.warning("mock-parts hash lookup failed partId=%s error=%s", part_id, type(exc).__name__)
    return _mock_get(settings.mock_parts_table, f"PARTS#{part_id}")


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


def _case_partition(case_id: str) -> list[dict]:
    from boto3.dynamodb.conditions import Key

    return _paginate(_table(c.CASES_TABLE).query, KeyConditionExpression=Key("pk").eq(f"CASE#{case_id}"))


def _by_prefix(items: list[dict], prefix: str) -> list[dict]:
    return sorted((i for i in items if str(i.get("sk", "")).startswith(prefix)), key=lambda i: i["sk"])


def _technician_appointments(technician_id: str) -> list[tuple[dict, dict]]:
    """Scan the cases table for every APPT# item owned by this technician, paired with its
    case's META. No technician index exists (API_requirements.md Section 2 note) -- a scan
    is the documented, accepted approach at demo volume."""
    from boto3.dynamodb.conditions import Attr

    appts = _paginate(
        _table(c.CASES_TABLE).scan,
        FilterExpression=Attr("sk").begins_with("APPT#") & Attr("technicianId").eq(technician_id)
        & Attr("status").ne("CANCELLED"),  # a cancelled visit (e.g. courier chosen) leaves the technician's list
    )
    out: list[tuple[dict, dict]] = []
    for appt in appts:
        case_id = str(appt["pk"]).removeprefix("CASE#")
        meta = next((i for i in _case_partition(case_id) if i.get("sk") == "META"), None)
        if meta is not None:
            out.append((appt, meta))
    return out


def _technician_closed_cases(technician_id: str) -> list[tuple[dict, dict]]:
    """Appointments COMPLETED (jobStatus) for this technician, with their case META."""
    return [(a, m) for a, m in _technician_appointments(technician_id) if a.get("status") == "COMPLETED"]


def _csat_scores(case_ids: list[str]) -> list[float]:
    """DynamoDB returns numbers as Decimal, not int/float -- isinstance(..., (int, float))
    would silently exclude those (same bug pattern as _parts_used_totals), so this checks
    numbers.Number instead."""
    import numbers

    scores: list[float] = []
    for cid in case_ids:
        for e in _by_prefix(_case_partition(cid), "EVT#"):
            if e.get("type") == _CSAT_EVT_TYPE:
                score = (e.get("detail") or {}).get("score")
                if isinstance(score, numbers.Number):
                    scores.append(float(score))
    return scores


# shortcut: static catalog (the opsresolve-dev-mock-manuals table does not exist), move to a table when more manuals exist.
_MANUALS = {
    "KB-CX500-017": {
        "title": "CX-500 troubleshooting guide",
        "excerpt": "Section 4.2: Control board replacement and compressor cycle verification.",
    },
}


def _resolve_manual_ref(ref: Any) -> dict | None:
    """manualRef as {title, excerpt}: stored either as a plain KB id (looked up in _MANUALS) or already as a map."""
    if not ref:
        return None
    if isinstance(ref, dict):
        return {"title": ref.get("title"), "excerpt": ref.get("excerpt")}
    known = _MANUALS.get(str(ref).strip())
    if known:
        return dict(known)
    return {"title": str(ref), "excerpt": None}


# --- 1. GET /mobile-web-app/me ------------------------------------------------------------


def _me(event: dict, technician_id: str, username: str) -> dict:
    claims = (event.get("requestContext") or {}).get("authorizer", {}).get("jwt", {}).get("claims") or {}
    tech = _mock_get(settings.mock_technicians_table, f"TECHNICIANS#{technician_id}") or {}
    name = tech.get("name") or claims.get("name") or username
    initials = "".join(p[0].upper() for p in name.split() if p)[:2] or None  # from the shown name, not the login (rohan_desai shows A. Iyer)

    appts = _technician_appointments(technician_id)
    week_ago = _now() - timedelta(days=7)
    jobs_this_week = sum(
        1 for a, _ in appts if (ts := _parse_ts(a.get("slotStart"))) is not None and ts >= week_ago
    )

    closed = _technician_closed_cases(technician_id)
    scores = _csat_scores([m["caseId"] for _, m in closed])
    rating = round(sum(scores) / len(scores), 1) if scores else None

    ftf_rates = tech.get("ftfRate") or {}
    main_model = next(iter((tech.get("certifications") or {}).keys()), None)
    ftf_rate = ftf_rates.get(main_model) if main_model else None
    try:
        ftf_rate = float(ftf_rate) if ftf_rate is not None else None
    except (TypeError, ValueError):
        ftf_rate = None

    certifications = [
        {"model": model, "validUntil": until} for model, until in (tech.get("certifications") or {}).items()
    ]
    skills = [{"skill": skill, "level": level} for skill, level in (tech.get("skills") or {}).items()]

    return {
        "technicianId": technician_id,
        "username": username,
        "name": name,
        "initials": initials,
        "title": None,  # API_requirements.md #1: no source; UI hides it
        "region": tech.get("region"),
        "shift": tech.get("shift"),
        "phone": tech.get("phone"),
        "homeDepotId": tech.get("homeDepotId"),  # None until the task-4 mock-data patch lands
        "certifications": certifications,
        "skills": skills,
        "stats": {
            "jobsThisWeek": jobs_this_week,
            "firstTimeFixRate": ftf_rate,
            "rating": rating,
        },
    }


# --- 2. GET /mobile-web-app/jobs ----------------------------------------------------------

_customers: dict[str, dict | None] = {}
_sites: dict[str, dict | None] = {}
_assets: dict[str, dict | None] = {}


def _customer(customer_id: str | None) -> dict | None:
    if not customer_id:
        return None
    if customer_id not in _customers:
        _customers[customer_id] = _mock_get(settings.mock_customers_table, f"CUSTOMERS#{customer_id}")
    return _customers[customer_id]


def _site(site_id: str | None) -> dict | None:
    if not site_id:
        return None
    if site_id not in _sites:
        _sites[site_id] = _mock_get(settings.mock_sites_table, f"SITES#{site_id}")
    return _sites[site_id]


def _asset(asset_id: str | None) -> dict | None:
    if not asset_id:
        return None
    if asset_id not in _assets:
        _assets[asset_id] = _mock_get(settings.mock_assets_table, f"ASSETS#{asset_id}")
    return _assets[asset_id]


def _job_card(appt: dict, meta: dict, case_id: str, rating_cache: dict[str, float | None]) -> dict:
    work_order_id = str(appt["sk"]).removeprefix("APPT#")
    cust = _customer(meta.get("customerId")) or {}
    asset = _asset(meta.get("assetId")) or {}
    site = _site(asset.get("siteId")) or {}
    status = appt.get("status")
    jstatus = c.job_status(status)

    rating = None
    if jstatus == "COMPLETED":
        if case_id not in rating_cache:
            scores = _csat_scores([case_id])
            rating_cache[case_id] = (sum(scores) / len(scores)) if scores else None
        rating = rating_cache[case_id]

    asset_label = None
    if asset.get("displayName") or asset.get("model"):
        asset_label = " · ".join(x for x in [asset.get("displayName"), asset.get("model")] if x)

    return {
        "jobId": case_id,
        "workOrderId": work_order_id,
        "customerName": cust.get("name"),
        "siteName": site.get("name"),
        "address": site.get("address"),
        "assetLabel": asset_label,
        "slotStart": appt.get("slotStart"),
        "slotEnd": appt.get("slotEnd"),
        "priority": meta.get("priority"),
        "jobStatus": jstatus,
        "distanceKm": None,  # static nav (decided)
        "completedAt": appt.get("completedAt"),
        "rating": round(rating, 1) if rating is not None else None,
    }


def _jobs(event: dict, technician_id: str) -> dict:
    qs = event.get("queryStringParameters") or {}
    view = qs.get("view") or "today"
    if view not in ("today", "upcoming", "completed"):
        raise _bad("'view' must be today, upcoming, or completed")

    appts = _technician_appointments(technician_id)
    now = _now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    rating_cache: dict[str, float | None] = {}
    cards_by_view: dict[str, list[dict]] = {"today": [], "upcoming": [], "completed": []}
    today_count = completed_count = 0
    next_start: str | None = None
    next_start_dt: datetime | None = None

    for appt, meta in appts:
        case_id = meta["caseId"]
        jstatus = c.job_status(appt.get("status"))
        slot_start = _parse_ts(appt.get("slotStart"))
        card = _job_card(appt, meta, case_id, rating_cache)

        if jstatus == "COMPLETED":
            completed_count += 1
            cards_by_view["completed"].append(card)
            continue
        if slot_start is None:
            continue
        if today_start <= slot_start < today_end:
            today_count += 1
            cards_by_view["today"].append(card)
            if next_start_dt is None or slot_start < next_start_dt:
                next_start_dt, next_start = slot_start, appt.get("slotStart")
        elif slot_start >= today_end:
            cards_by_view["upcoming"].append(card)

    cards_by_view["today"].sort(key=lambda j: j.get("slotStart") or "")
    cards_by_view["upcoming"].sort(key=lambda j: j.get("slotStart") or "")
    cards_by_view["completed"].sort(key=lambda j: j.get("completedAt") or "", reverse=True)

    return {
        "view": view,
        "jobs": cards_by_view[view],
        "counts": {"today": today_count, "completed": completed_count},
        "nextStart": next_start,
    }


# --- 3. GET /mobile-web-app/jobs/{jobId} --------------------------------------------------


def _job_id(event: dict) -> str:
    jid = (event.get("pathParameters") or {}).get("jobId") or ""
    if not jid:
        raise _bad("Missing jobId in path")
    return jid


def _diagnosis_history(events: list[dict]) -> list[dict]:
    out = []
    for e in events:
        etype = e.get("type")
        if etype == "TOOL_CALLED":
            detail = e.get("detail") or {}
            specialist = detail.get("specialist") or (e.get("actor") or {}).get("name") or "Agent"
            status = detail.get("status", "OK")
            tool = e.get("toolName") or "tool"
            out.append({"at": str(e["sk"]).split("#", 2)[1], "summary": f"{specialist} called {tool} ({status})"})
        elif etype == "SPECIALIST_DELEGATED":
            name = (e.get("detail") or {}).get("specialist") or (e.get("actor") or {}).get("name") or "Specialist"
            out.append({"at": str(e["sk"]).split("#", 2)[1], "summary": f"Delegated to {name}"})
        elif etype == "CASE_STATE_CHANGED":
            d = e.get("detail") or {}
            out.append({"at": str(e["sk"]).split("#", 2)[1], "summary": f"{d.get('from')} -> {d.get('to')}"})
    return out


def _parts_used_totals(events: list[dict]) -> dict[str, int]:
    """Sum qty from every PART_USED timeline event on this case, keyed by partId. DynamoDB
    returns numbers as Decimal, not int/float -- isinstance(..., (int, float)) silently
    excludes those, so this checks numbers.Number instead (covers int/float/Decimal)."""
    import numbers

    totals: dict[str, int] = {}
    for e in events:
        if e.get("type") != "PART_USED":
            continue
        detail = e.get("detail") or {}
        part_id = detail.get("partId")
        qty = detail.get("qty")
        if part_id and isinstance(qty, numbers.Number):
            totals[part_id] = totals.get(part_id, 0) + int(qty)
    return totals


def _required_part_specs(required: Any) -> list[dict]:
    """Normalize META.requiredPart into a list of {partId, name, qty} specs. Supports all 3
    shapes: a single dict (original, most seeded cases), a bare partId string, or a list of
    dicts (new -- multiple required parts per job)."""
    if not required:
        return []
    if isinstance(required, list):
        specs = []
        for item in required:
            if isinstance(item, dict):
                part_id = item.get("partId")
                if part_id:
                    specs.append({"partId": part_id, "name": item.get("name") or part_id, "qty": item.get("qty", 1)})
            elif item:
                specs.append({"partId": str(item), "name": str(item), "qty": 1})
        return specs
    if isinstance(required, dict):
        part_id = required.get("partId")
        return [{"partId": part_id, "name": required.get("name") or part_id, "qty": required.get("qty", 1)}]
    return [{"partId": str(required), "name": str(required), "qty": 1}]


def _required_parts(meta: dict, events: list[dict] | None = None) -> list[dict]:
    """requiredParts is computed, not static: qtyRequired comes from META.requiredPart
    (single part or a list of parts -- both supported), qtyUsed is the running total from
    PART_USED events on this case per partId, qtyRemaining = max(qtyRequired - qtyUsed, 0).
    A part stays in the list even once fully used (qtyRemaining 0) so the UI can show it as
    done rather than have it vanish mid-job."""
    specs = _required_part_specs(meta.get("requiredPart"))
    if not specs:
        return []

    used_totals = _parts_used_totals(events or [])
    out = []
    for spec in specs:
        qty_required = spec["qty"]
        qty_used = used_totals.get(spec["partId"], 0)
        qty_remaining = max(qty_required - qty_used, 0)
        out.append({
            "partId": spec["partId"],
            "name": spec["name"],
            "qty": qty_required,  # kept for backward compatibility with existing clients
            "qtyRequired": qty_required,
            "qtyUsed": qty_used,
            "qtyRemaining": qty_remaining,
        })
    return out


_PART_REQUEST_PREFIX = "PART_REQUEST:"


def _part_requests(case_id: str) -> list[dict]:
    """The case's technician part requests (approvals with action PART_REQUEST:<partId>), newest first, with
    their decision status (PENDING / APPROVED / REJECTED). The approvals table has no caseId index: scan, as the
    console does. Any read error degrades to [] so the job detail still loads (needs approvals Scan on the
    field-read role)."""
    from boto3.dynamodb.conditions import Attr

    try:
        rows = _paginate(
            _table(c.APPROVALS_TABLE).scan,
            FilterExpression=Attr("caseId").eq(case_id) & Attr("action").begins_with(_PART_REQUEST_PREFIX),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"part requests unavailable for {case_id}: {exc}")
        return []
    rows.sort(key=lambda r: str(r.get("createdAt") or ""), reverse=True)
    return [
        {
            "approvalId": r.get("approvalId"),
            "partId": str(r.get("action") or "").removeprefix(_PART_REQUEST_PREFIX),
            "qty": r.get("qty"),
            "note": r.get("note"),
            "status": r.get("status"),
            "requestedBy": r.get("requestedBy"),
            "createdAt": r.get("createdAt"),
            "decidedAt": r.get("decidedAt"),
            "reason": r.get("reason"),
        }
        for r in rows
    ]


def _job_detail(event: dict, technician_id: str) -> dict:
    case_id = _job_id(event)
    items = _case_partition(case_id)
    meta = next((i for i in items if i.get("sk") == "META"), None)
    if meta is None:
        raise _not_found(f"job {case_id} not found")

    appts = _by_prefix(items, "APPT#")
    appt = next((a for a in appts if a.get("technicianId") == technician_id), None)
    if appt is None:
        raise _forbidden("job is not assigned to the caller")

    events = [dict(i) for i in _by_prefix(items, "EVT#")]
    for e in events:
        e["sk"] = e["sk"]  # keep raw sk for the "at" split above

    plans = _by_prefix(items, "PLAN#")
    latest_plan = plans[-1] if plans else None

    cust = _customer(meta.get("customerId")) or {}
    asset = _asset(meta.get("assetId")) or {}
    site = _site(asset.get("siteId")) or {}

    from boto3.dynamodb.conditions import Attr

    contacts = _paginate(
        _table(settings.mock_contacts_table).scan,
        FilterExpression=Attr("customerId").eq(meta.get("customerId")) | Attr("siteId").eq(asset.get("siteId")),
    ) if meta.get("customerId") or asset.get("siteId") else []
    primary_contact = next((k for k in contacts if k.get("isPrimary") is True), contacts[0] if contacts else None)

    field_brief = appt.get("fieldBrief") or {}
    if not field_brief and latest_plan:
        field_brief = {"text": latest_plan.get("rationale"), "confidence": None}

    return {
        "jobId": case_id,
        "workOrderId": str(appt["sk"]).removeprefix("APPT#"),
        "caseState": meta.get("state"),
        "jobStatus": c.job_status(appt.get("status")),
        "priority": meta.get("priority"),
        "slotStart": appt.get("slotStart"),
        "slotEnd": appt.get("slotEnd"),
        "customer": {"customerId": meta.get("customerId"), "name": cust.get("name")},
        "contact": None if primary_contact is None else {
            "contactId": primary_contact.get("contactId"),
            "name": primary_contact.get("name"),
            "role": primary_contact.get("role"),
            "phone": primary_contact.get("phone"),
        },
        "site": {
            "siteId": asset.get("siteId"), "name": site.get("name"), "address": site.get("address"),
            "timezone": site.get("timezone"), "lat": site.get("lat"), "lon": site.get("lon"),
        },
        "asset": {
            "assetId": meta.get("assetId"), "displayName": asset.get("displayName"),
            "model": asset.get("model"), "serial": asset.get("serial"),
        },
        "safetyNote": site.get("accessNotes"),  # no per-job field yet (spec note #3)
        "brief": field_brief or None,
        "symptoms": meta.get("customerDescription"),
        "diagnosisHistory": _diagnosis_history(events),
        "requiredParts": _required_parts(meta, events),
        "checklist": appt.get("checklist") or [],
        "manualRef": _resolve_manual_ref(appt.get("manualRef")),
        "partRequests": _part_requests(case_id),
        "partsUsed": [
            {
                "partId": (e.get("detail") or {}).get("partId"),
                "qty": (e.get("detail") or {}).get("qty"),
                "usedAt": (e.get("detail") or {}).get("usedAt"),
            }
            for e in events
            if e.get("type") == "PART_USED"
        ],
    }


# --- 5. GET /mobile-web-app/depot-stock ---------------------------------------------------


def _stock_level(available: int) -> str:
    if available <= 0:
        return "UNAVAILABLE"
    if available == 1:
        return "LOW"
    return "AVAILABLE"


def _depot_stock(event: dict, technician_id: str) -> dict:
    qs = event.get("queryStringParameters") or {}
    job_id = qs.get("jobId")

    tech = _mock_get(settings.mock_technicians_table, f"TECHNICIANS#{technician_id}") or {}
    depot_id = tech.get("homeDepotId") or "DEP-CHN-01"  # fallback until task-4 mock patch lands
    depot = _mock_get(settings.mock_depots_table, f"DEPOTS#{depot_id}") or {"depotId": depot_id}

    from boto3.dynamodb.conditions import Attr

    inv_items = _paginate(_table(settings.mock_inventory_table).scan, FilterExpression=Attr("depotId").eq(depot_id))

    def _part_view(inv: dict) -> dict:
        part_id = inv.get("partId")
        part = _mock_get_part(part_id) if part_id else None
        on_hand = int(inv.get("onHand", 0))
        reserved = int(inv.get("reserved", 0))
        available = max(on_hand - reserved, 0)
        name = (part or {}).get("name") or part_id
        return {
            "partId": part_id,
            "name": name,
            "description": (part or {}).get("description"),
            "onHand": on_hand,
            "reserved": reserved,
            "available": available,
            "stockLevel": _stock_level(available),
        }

    items = [_part_view(i) for i in inv_items]

    required_parts: list[dict] = []
    if job_id:
        case_items = _case_partition(job_id)
        meta = next((i for i in case_items if i.get("sk") == "META"), None)
        if meta is not None:
            events = _by_prefix(case_items, "EVT#")
            for rp in _required_parts(meta, events):
                match = next((i for i in items if i["partId"] == rp["partId"]), None)
                qty_fields = {
                    "qtyRequired": rp["qtyRequired"], "qtyUsed": rp["qtyUsed"], "qtyRemaining": rp["qtyRemaining"],
                }
                if match:
                    required_parts.append({
                        "partId": rp["partId"], "name": match["name"], **qty_fields,
                        "onHand": match["onHand"], "reserved": match["reserved"], "available": match["available"],
                        "stockLevel": match["stockLevel"],
                    })
                else:
                    required_parts.append({
                        "partId": rp["partId"], "name": rp.get("name") or rp["partId"], **qty_fields,
                        "onHand": 0, "reserved": 0, "available": 0, "stockLevel": "UNAVAILABLE",
                    })

    return {
        "depot": {"depotId": depot.get("depotId", depot_id), "name": depot.get("name")},
        "requiredParts": required_parts,
        "items": items,
    }


# --- 8. GET /mobile-web-app/parts/{partId} (optional, low priority) ----------------------


def _part_lookup(event: dict, technician_id: str) -> dict:
    part_id = (event.get("pathParameters") or {}).get("partId") or ""
    if not part_id:
        raise _bad("Missing partId in path")
    part = _mock_get_part(part_id)
    if part is None:
        raise _not_found(f"part {part_id} not found")

    tech = _mock_get(settings.mock_technicians_table, f"TECHNICIANS#{technician_id}") or {}
    depot_id = tech.get("homeDepotId") or "DEP-CHN-01"
    from boto3.dynamodb.conditions import Key

    inv = _table(settings.mock_inventory_table).get_item(
        Key={"pk": f"INV#{depot_id}#{part_id}", "sk": "META"}
    ).get("Item")
    on_hand = int((inv or {}).get("onHand", 0))
    reserved = int((inv or {}).get("reserved", 0))
    available = max(on_hand - reserved, 0)

    return {
        "partId": part_id,
        "name": part.get("name"),
        "description": part.get("description"),
        "unitValueUsd": part.get("unitValueUsd"),
        "compatibleModels": part.get("compatibleModels", []),
        "depotStock": {"depotId": depot_id, "onHand": on_hand, "available": available},
    }


# --- 12. GET /mobile-web-app/availability (technician-availability-scheduling spec) -------


def _availability(event: dict, technician_id: str) -> list[dict]:
    from boto3.dynamodb.conditions import Key

    qs = event.get("queryStringParameters") or {}
    date_filter = qs.get("date")  # e.g. "2025-01-15" -- narrows to slots on that date only
    status_filter = qs.get("status")  # optional AVAILABLE|BOOKED filter

    # Push date filtering into DynamoDB via begins_with on the sort key.
    # Table sk schema: "SLOT#<date>#<slotId>" (see config.py field_availability_table comment).
    # When a date is supplied we query only that date's prefix; otherwise we fetch all slots.
    if date_filter:
        key_cond = (
            Key("pk").eq(f"TECHNICIAN#{technician_id}")
            & Key("sk").begins_with(f"SLOT#{date_filter}#")
        )
    else:
        key_cond = Key("pk").eq(f"TECHNICIAN#{technician_id}")

    items = _paginate(
        _table(settings.field_availability_table).query,
        KeyConditionExpression=key_cond,
    )

    if status_filter:
        items = [i for i in items if i.get("status") == status_filter]

    return [
        {
            "slotId": i.get("slotId"),
            "technicianId": i.get("technicianId"),
            "date": i.get("date"),
            "start": i.get("start"),
            "end": i.get("end"),
            "note": i.get("note"),
            "createdAt": i.get("createdAt"),
            "status": i.get("status"),
            "appointmentId": i.get("appointmentId"),
        }
        for i in items
    ]


# --- dispatch ------------------------------------------------------------------------------

_ROUTES: dict[str, Callable[[dict, str], dict]] = {
    "GET /mobile-web-app/me": lambda event, tid: _me(event, tid, c.technician_identity(event)[1]),
    "GET /mobile-web-app/jobs": lambda event, tid: _jobs(event, tid),
    "GET /mobile-web-app/jobs/{jobId}": lambda event, tid: _job_detail(event, tid),
    "GET /mobile-web-app/depot-stock": lambda event, tid: _depot_stock(event, tid),
    "GET /mobile-web-app/parts/{partId}": lambda event, tid: _part_lookup(event, tid),
    "GET /mobile-web-app/availability": lambda event, tid: _availability(event, tid),
}


def lambda_handler(event: dict, context: Any = None) -> dict:
    _customers.clear()
    _sites.clear()
    _assets.clear()

    route_key = event.get("routeKey", "")
    route = _ROUTES.get(route_key)
    if route is None:
        return c.error(404, "NOT_FOUND", f"Unknown route {route_key}")

    try:
        technician_id, _ = c.technician_identity(event)
    except c.AuthError as exc:
        return c.error(exc.status, exc.code, exc.message)

    try:
        body = route(event, technician_id)
    except _HttpError as exc:
        return c.error(exc.status, exc.code, exc.message)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"field_read {route_key} failed: {exc}")
        return c.error(500, "INTERNAL_ERROR", "Internal error")
    return c.ok(body)
