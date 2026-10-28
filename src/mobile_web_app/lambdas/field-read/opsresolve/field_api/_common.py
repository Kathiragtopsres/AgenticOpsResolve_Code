"""Shared contract for the four OpsResolve Field Lambdas (read / actions / uploads / assistant).

Mirrors ``console_api/_common.py`` (same error shape, same ``ok``/``error``/``parse_json_body``
helpers) plus the field-app-specific pieces: the signed-in
technician (``custom:id``) and tenant (``custom:tenantId``) claim readers. Downstream field handlers MUST read the constants below
rather than re-invent them -- see ``API_requirements.md`` Section 0.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Any

from ..common.config import settings

# --- Resource names ----------------------------------------------------------------------
CASES_TABLE = settings.cases_table
APPROVALS_TABLE = settings.approvals_table
IDEMPOTENCY_TABLE = settings.idempotency_table
BUS_NAME = settings.bus_name  # "opsresolve-dev-bus" (common/events.py's BUS_NAME is wrong)

# --- jobStatus mapping (API_requirements.md Section 0 "Conventions") ----------------------
APPT_STATUS_TO_JOB_STATUS: dict[str, str] = {
    "SCHEDULED": "NOT_STARTED",
    "DELAYED": "NOT_STARTED",
    "EN_ROUTE": "EN_ROUTE",
    "IN_PROGRESS": "ON_SITE",
    "ON_SITE": "ON_SITE",
    "COMPLETED": "COMPLETED",
}

PRIORITY_LABEL: dict[str, str] = {"P1": "Critical", "P2": "High", "P3": "Medium", "P4": "Low"}

CORS_HEADERS = {"content-type": "application/json"}


def job_status(appt_status: str | None) -> str:
    return APPT_STATUS_TO_JOB_STATUS.get(appt_status or "", "NOT_STARTED")


def _json_default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    raise TypeError(f"not JSON serializable: {type(o).__name__}")


def ok(body: Any, status: int = 200) -> dict:
    """API Gateway HTTP API v2 (payload 2.0) success response."""
    return {
        "statusCode": status,
        "headers": CORS_HEADERS,
        "body": json.dumps(body, default=_json_default),
    }


def error(status: int, code: str, message: str) -> dict:
    """Error response: {"error": {"code", "message"}}. Never include stack traces."""
    return {
        "statusCode": status,
        "headers": CORS_HEADERS,
        "body": json.dumps({"error": {"code": code, "message": message}}),
    }


def parse_json_body(event: dict) -> dict | None:
    """Return the JSON object body, or None if missing/invalid/not an object."""
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


class AuthError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def jwt_claims(event: dict) -> dict:
    """Claims the API Gateway JWT authorizer verified (ID token), or {}."""
    return (event.get("requestContext") or {}).get("authorizer", {}).get("jwt", {}).get("claims") or {}


def caller_tenant(event: dict) -> str | None:
    """Tenant of the signed-in user (claim ``custom:tenantId``); None without a verified token."""
    return jwt_claims(event).get("custom:tenantId") or None


def _requested_technician(event: dict) -> str | None:
    qs = event.get("queryStringParameters") or {}
    return qs.get("technicianId") or (parse_json_body(event) or {}).get("technicianId")


def technician_identity(event: dict) -> tuple[str, str]:
    """Return ``(technicianId, username)`` for the caller.

    technicianId is the verified ID-token claim ``custom:id``. A ``technicianId`` in the request that differs
    from it is a 403. Only with env ``ALLOW_UNAUTHENTICATED=true`` (local tests, default off) and no claim is the
    technicianId read from the request, as before the authorizer was attached.
    """
    claims = jwt_claims(event)
    claimed, requested = claims.get("custom:id"), _requested_technician(event)
    if claimed:
        if requested and requested != claimed:
            raise AuthError(403, "FORBIDDEN", "technicianId does not match the signed-in technician")
        return claimed, claims.get("cognito:username") or claimed
    if os.getenv("ALLOW_UNAUTHENTICATED", "").lower() == "true" and requested:
        return requested, requested
    raise AuthError(401, "UNAUTHORIZED", "token has no technician id (custom:id)")
