"""Canonical enumerations shared across the platform.

These mirror the Technical Specification (Sections 5–10) and the reference oracles in
`reference/opsresolve_ref/`. Keep values in sync — contract tests compare production
behaviour against the reference implementations.
"""
from __future__ import annotations

from enum import Enum


class CaseState(str, Enum):
    """Service case states (Section 8.2)."""

    OPEN = "OPEN"
    CONTEXT_READY = "CONTEXT_READY"
    DIAGNOSING = "DIAGNOSING"
    AWAITING_CUSTOMER = "AWAITING_CUSTOMER"
    REMOTE_FIX_IN_PROGRESS = "REMOTE_FIX_IN_PROGRESS"
    RESOLVED_REMOTE = "RESOLVED_REMOTE"
    PLANNING_FIELD = "PLANNING_FIELD"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    SCHEDULED = "SCHEDULED"
    AT_RISK = "AT_RISK"
    IN_SERVICE = "IN_SERVICE"
    VERIFYING = "VERIFYING"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    ESCALATED_HUMAN = "ESCALATED_HUMAN"


class Channel(str, Enum):
    """AWS CDS channels (Section 7.1)."""

    RCS = "RCS"
    WHATSAPP = "WHATSAPP"
    SMS = "SMS"
    EMAIL = "EMAIL"


class MessageIntent(str, Enum):
    """Customer-communication intents used by sendCustomerMessage (Sections 6.3, 7.8)."""

    APPOINTMENT_OPTIONS = "APPOINTMENT_OPTIONS"
    CONFIRMATION = "CONFIRMATION"
    RECOVERY_OFFER = "RECOVERY_OFFER"
    ETA_UPDATE = "ETA_UPDATE"
    CLARIFY = "CLARIFY"
    REPORT = "REPORT"
    CSAT = "CSAT"
    URGENT_ALERT = "URGENT_ALERT"
    APPROVAL_REQUEST = "APPROVAL_REQUEST"


class ConsentState(str, Enum):
    OPTED_IN = "OPTED_IN"
    OPTED_OUT = "OPTED_OUT"
    UNKNOWN = "UNKNOWN"


class AutonomyLevel(str, Enum):
    """Autonomy levels (Section 1.5 / 10.1)."""

    L0 = "L0"  # read-only / deterministic
    L1 = "L1"  # ranking / offer
    L2 = "L2"  # autonomous within policy
    L3 = "L3"  # conditional auto-apply
    L4 = "L4"  # human required


class SafeRemoteAction(str, Enum):
    """Allow-listed safe remote actions (Section 6.3 / 10.2)."""

    CONTROLLER_SOFT_RESET = "CONTROLLER_SOFT_RESET"
    RECALIBRATE_SENSOR = "RECALIBRATE_SENSOR"
    SET_ECO_MODE = "SET_ECO_MODE"


class ToolErrorCode(str, Enum):
    """Standard tool error codes (Section 6.5)."""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT_STATE = "CONFLICT_STATE"
    PART_UNAVAILABLE = "PART_UNAVAILABLE"
    NO_TECHNICIAN = "NO_TECHNICIAN"
    POLICY_DENIED = "POLICY_DENIED"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"
    DOWNSTREAM_TIMEOUT = "DOWNSTREAM_TIMEOUT"  # retryable
    THROTTLED = "THROTTLED"  # retryable
    CHANNEL_NOT_CONSENTED = "CHANNEL_NOT_CONSENTED"
    CHANNEL_UNAVAILABLE = "CHANNEL_UNAVAILABLE"


RETRYABLE_ERRORS = {ToolErrorCode.DOWNSTREAM_TIMEOUT, ToolErrorCode.THROTTLED}
TERMINAL_STATES = {CaseState.CLOSED, CaseState.CANCELLED}
