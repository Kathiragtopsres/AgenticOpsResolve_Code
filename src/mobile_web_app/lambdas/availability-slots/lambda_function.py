"""Standalone Lambda: POST /mobile-web-app/availability-summary

Returns a per-day count of AVAILABLE and BOOKED slots for a given technician
from a start date through the end of that calendar month.

Handler: availability_summary.handler   (no package prefix — fully standalone)

Request body (JSON):
    {
        "technician_id": "<id>",
        "date":          "YYYY-MM-DD"   # start date (inclusive)
    }

Response body (JSON array, HTTP 200):
    [
        { "date": "YYYY-MM-DD", "available_slot_count": 3, "booked_slot_count": 1 },
        { "date": "YYYY-MM-DD", "available_slot_count": 0, "booked_slot_count": 2 },
        ...
    ]

Every calendar day from `date` through the last day of that month is always
present in the output, even when no slots exist for that day (counts default to 0).

DynamoDB table  : opsresolve-{ENV}-field-availability
    pk  = "TECHNICIAN#<technicianId>"
    sk  = "SLOT#YYYY-MM-DD#<slotId>"
    attr: status  = "AVAILABLE" | "BOOKED"
"""
from __future__ import annotations

import calendar
import json
import logging
import os
from datetime import date, timedelta
from decimal import Decimal
from functools import lru_cache
from typing import Any

# ---------------------------------------------------------------------------
# Configuration  (mirrors common/config.py Settings, inlined here)
# ---------------------------------------------------------------------------

_ENV    = os.getenv("OPSRESOLVE_ENV", "dev")
_REGION = os.getenv("AWS_REGION", "us-east-1")

# DynamoDB table name
FIELD_AVAILABILITY_TABLE = "opsresolve-dev-field-availability"

# HTTP response headers
_CORS_HEADERS = {"content-type": "application/json"}

# ---------------------------------------------------------------------------
# Logging  (mirrors common/logging.py get_logger, inlined here)
# ---------------------------------------------------------------------------

def _get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter('{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


logger = _get_logger(__name__)

# ---------------------------------------------------------------------------
# HTTP response helpers  (mirrors _common.py ok / error, inlined here)
# ---------------------------------------------------------------------------

def _json_default(o: Any) -> Any:
    """JSON serializer fallback: Decimal → int/float, set → sorted list."""
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    raise TypeError(f"not JSON serializable: {type(o).__name__}")


def _ok(body: Any, status: int = 200) -> dict:
    """API Gateway HTTP API v2 (payload 2.0) success response."""
    return {
        "statusCode": status,
        "headers": _CORS_HEADERS,
        "body": json.dumps(body, default=_json_default),
    }


def _error(status: int, code: str, message: str) -> dict:
    """Error response shape: { "error": { "code": ..., "message": ... } }"""
    return {
        "statusCode": status,
        "headers": _CORS_HEADERS,
        "body": json.dumps({"error": {"code": code, "message": message}}),
    }


def _parse_json_body(event: dict) -> dict | None:
    """Extract and parse the JSON body from an API Gateway v2 event."""
    raw = event.get("body")
    if not raw:
        return None
    if event.get("isBase64Encoded"):
        import base64
        raw = base64.b64decode(raw).decode("utf-8", "replace")
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


# ---------------------------------------------------------------------------
# DynamoDB helpers
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _ddb():  # pragma: no cover - thin SDK wrapper
    import boto3
    return boto3.resource("dynamodb", region_name=_REGION)


def _table(name: str):
    return _ddb().Table(name)


def _paginate(fn, **kwargs) -> list[dict]:
    """Exhaust a DynamoDB paginated operation and return all Items."""
    items: list[dict] = []
    while True:
        resp = fn(**kwargs)
        items.extend(resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            return items
        kwargs["ExclusiveStartKey"] = lek


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _parse_date(value: str) -> date:
    """Parse an ISO-8601 date string (YYYY-MM-DD) into a date object."""
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid date '{value}'. Expected YYYY-MM-DD format.")


def _month_end(d: date) -> date:
    """Return the last calendar day of the month that `d` falls in."""
    last_day = calendar.monthrange(d.year, d.month)[1]
    return date(d.year, d.month, last_day)


def _date_range(start: date, end: date) -> list[date]:
    """Return every date from start through end (both inclusive)."""
    days: list[date] = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def _fetch_slots(technician_id: str, start: date, end: date) -> list[dict]:
    """Query the field-availability table for all slots in [start, end].

    Uses a DynamoDB range condition on sk so only the relevant date window
    is transferred from DynamoDB — no full-partition scan required.

    sk format: "SLOT#YYYY-MM-DD#<slotId>"
    Upper bound uses '~' (ASCII 126) which sorts after all alphanumerics,
    ensuring the range captures every slotId on end_date.
    """
    from boto3.dynamodb.conditions import Key

    return _paginate(
        _table(FIELD_AVAILABILITY_TABLE).query,
        KeyConditionExpression=(
            Key("pk").eq(f"TECHNICIAN#{technician_id}")
            & Key("sk").between(
                f"SLOT#{start.isoformat()}",
                f"SLOT#{end.isoformat()}~",
            )
        ),
    )


def _build_summary(slots: list[dict], days: list[date]) -> list[dict]:
    """Aggregate slot status counts per day.

    Every day in `days` is always present in the output.
    Unknown status values are logged and skipped (never a 500).
    """
    # Pre-populate every day in the range with zero counts.
    counts: dict[str, dict[str, int]] = {
        d.isoformat(): {"available_slot_count": 0, "booked_slot_count": 0}
        for d in days
    }

    for slot in slots:
        # sk = "SLOT#YYYY-MM-DD#<slotId>" → split gives ["SLOT", "YYYY-MM-DD", "<slotId>"]
        sk = str(slot.get("sk", ""))
        parts = sk.split("#")
        if len(parts) < 3:
            logger.warning("availability_summary: unexpected sk format sk=%s", sk)
            continue

        slot_date = parts[1]
        if slot_date not in counts:
            # Outside the requested range — skip gracefully.
            continue

        status = slot.get("status", "")
        if status == "AVAILABLE":
            counts[slot_date]["available_slot_count"] += 1
        elif status == "BOOKED":
            counts[slot_date]["booked_slot_count"] += 1
        else:
            logger.warning(
                "availability_summary: unknown status='%s' sk=%s", status, sk
            )

    # Return sorted by date ascending.
    return [{"date": d, **v} for d, v in sorted(counts.items())]


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------

def lambda_handler(event: dict, context: Any = None) -> dict:
    """Lambda entry point.

    Parses `technician_id` and `date` from the JSON request body, queries
    the field-availability table, and returns per-day AVAILABLE / BOOKED
    counts from that date through the end of the same calendar month.

    Sample event (HTTP API payload v2):
    {
        "body": "{\"technician_id\": \"TECH-001\", \"date\": \"2026-10-13\"}",
        "isBase64Encoded": false
    }
    """
    print("event",event)
    body = _parse_json_body(event) or {}

    claims = (event.get("requestContext") or {}).get("authorizer", {}).get("jwt", {}).get("claims") or {}
    claimed: str = claims.get("custom:id") or ""
    technician_id: str = body.get("technician_id") or ""
    date_str: str      = body.get("date") or ""

    # The signed-in technician (ID-token claim custom:id) is the only one whose availability is served.
    # ALLOW_UNAUTHENTICATED=true (local tests, default off) keeps the old request-body behaviour.
    if claimed:
        if technician_id and technician_id != claimed:
            return _error(403, "FORBIDDEN", "technician_id does not match the signed-in technician.")
        technician_id = claimed
    elif os.environ.get("ALLOW_UNAUTHENTICATED", "").lower() != "true":
        return _error(401, "UNAUTHORIZED", "Token has no technician id (custom:id).")

    # --- Validate inputs -------------------------------------------------------
    if not technician_id:
        return _error(400, "BAD_REQUEST", "'technician_id' is required in the request body.")
    if not date_str:
        return _error(400, "BAD_REQUEST", "'date' is required in the request body.")

    try:
        start_date = _parse_date(date_str)
    except ValueError as exc:
        return _error(400, "BAD_REQUEST", str(exc))

    # --- Date range: start_date → last day of that month -----------------------
    end_date = _month_end(start_date)
    days     = _date_range(start_date, end_date)

    # --- Fetch & aggregate -----------------------------------------------------
    try:
        slots = _fetch_slots(technician_id, start_date, end_date)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "availability_summary: fetch failed technician_id=%s date=%s error=%s",
            technician_id, date_str, exc,
        )
        return _error(500, "INTERNAL_ERROR", "Failed to fetch availability data.")

    summary = _build_summary(slots, days)
    return _ok(summary)
