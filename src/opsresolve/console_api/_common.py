"""Shared contract for the three Ops Console Lambdas (read / approval-decision / scenario).

Downstream handlers MUST read the PINNED constants below rather than re-invent them.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from ..common.config import settings

# --- Resource names (bus name lives in common/config.py as settings.bus_name) -----------
CASES_TABLE = settings.cases_table
APPROVALS_TABLE = settings.approvals_table
CONVERSATIONS_TABLE = settings.conversations_table
BUS_NAME = settings.bus_name  # "opsresolve-dev-bus"

# --- UI column -> case states (tasks.md, locked) ---------------------------------------
COLUMN_STATES: dict[str, list[str]] = {
    "Open": ["OPEN", "CONTEXT_READY"],
    "Diagnosing": [
        "DIAGNOSING", "AWAITING_CUSTOMER", "AWAITING_APPROVAL",
        "REMOTE_FIX_IN_PROGRESS", "PLANNING_FIELD",
    ],
    "Scheduled": ["SCHEDULED"],
    "At Risk": ["AT_RISK", "ESCALATED_HUMAN"],
    "In Service": ["IN_SERVICE", "RESOLVED_REMOTE", "VERIFYING"],
    "Closed": ["CLOSED", "CANCELLED"],
}
STATE_TO_COLUMN: dict[str, str] = {s: c for c, states in COLUMN_STATES.items() for s in states}

# --- PINNED CONTRACT: KPI timeline event types -----------------------------------------
# Stored in the cases table as ordinary timeline items (pk=CASE#<id>, sk=EVT#<ts>#<id>)
# whose ``type`` attribute is one of these. The read Lambda counts them; the seed writes them.
#   CSAT_RECEIVED       detail = {"score": <int 1..5>}
#   SLA_BREACH_AVOIDED  detail = {"restoreBy": <ISO-8601>, "recoveredAt": <ISO-8601>}
EVT_CSAT = "CSAT_RECEIVED"
EVT_SLA_BREACH_AVOIDED = "SLA_BREACH_AVOIDED"

# --- PINNED CONTRACT: messages ----------------------------------------------------------
# Stored in the CASES table (NOT conversations), same single-table layout as case_store.py:
#   pk = "CASE#<caseId>", sk = "MSG#<ts>#<messageId>"   (ts = ISO-8601 UTC)
#   attributes: channel (RCS|SMS|WHATSAPP|EMAIL), direction (OUTBOUND|INBOUND),
#               body (str), sentAt (ISO-8601, == ts), messageId (str)
# A card's channel = ``channel`` of the latest MSG# item (max sk).
MSG_SK_PREFIX = "MSG#"
MSG_ATTRS = ("messageId", "channel", "direction", "body", "sentAt")

# --- PINNED CONTRACT: approvals (opsresolve-dev-approvals, hash key approvalId only) ----
# Lakshmi's requestApproval writes: approvalId, caseId, action, amount (str), approverRole,
# notifiedApprover, status (PENDING), idempotencyKey, createdAt.
# Console decision adds: reason, decidedBy, decidedAt (status -> APPROVED | REJECTED).
# Technician part requests (mobile app, action PART_REQUEST:<partId>) also carry requestedBy (technicianId),
# qty, note (optional) and source = FIELD_APP; null on agent-raised approvals.
# UI status filter: PENDING -> status=PENDING ; DECIDED -> status in (APPROVED, REJECTED).
APPROVAL_PK = "approvalId"
APPROVAL_STATUS_PENDING = "PENDING"
APPROVAL_STATUS_APPROVED = "APPROVED"
APPROVAL_STATUS_REJECTED = "REJECTED"
APPROVAL_DECIDED_STATUSES = (APPROVAL_STATUS_APPROVED, APPROVAL_STATUS_REJECTED)
APPROVAL_ATTRS = (
    "approvalId", "caseId", "action", "amount", "approverRole", "notifiedApprover",
    "status", "idempotencyKey", "createdAt", "reason", "decidedBy", "decidedAt",
    "requestedBy", "qty", "note", "source",
)
APPROVAL_ACTION_PART_REQUEST = "PART_REQUEST:"  # + partId (technician part request from the mobile app)

CORS_HEADERS = {"content-type": "application/json"}  # CORS itself is configured on the HTTP API


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


def jwt_username(event: dict) -> str | None:
    """Cognito username from the JWT authorizer claims (ID token: ``cognito:username``)."""
    claims = (
        (event.get("requestContext") or {}).get("authorizer", {}).get("jwt", {}).get("claims") or {}
    )
    return claims.get("cognito:username")


NO_TENANT_MESSAGE = "token has no tenant (custom:tenantId)"


def tenant_id(event: dict) -> str | None:
    """Caller's tenant from the JWT authorizer claims (ID token: ``custom:tenantId``). None = no claim; callers
    answer 403, there is no default for console users."""
    claims = (
        (event.get("requestContext") or {}).get("authorizer", {}).get("jwt", {}).get("claims") or {}
    )
    return claims.get("custom:tenantId") or None


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
