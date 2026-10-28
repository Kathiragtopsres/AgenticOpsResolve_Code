"""Shared adapter helpers: error envelope, idempotency, retry policy (Section 6.5).

Adapters implement a common tool contract. A production adapter adds authentication,
field mapping, retry/backoff and circuit breaking; the agent layer is unchanged.
"""
from __future__ import annotations

from typing import Any

from ..common.enums import RETRYABLE_ERRORS, ToolErrorCode


def error_envelope(
    code: ToolErrorCode,
    message: str,
    correlation_id: str,
    details: dict | None = None,
) -> dict[str, Any]:
    """Build the standard tool error envelope (Listing 6-5)."""
    return {
        "error": {
            "code": code.value,
            "retryable": code in RETRYABLE_ERRORS,
            "message": message,
            "details": details or {},
            "correlationId": correlation_id,
        }
    }


def is_retryable(code: ToolErrorCode) -> bool:
    return code in RETRYABLE_ERRORS


# Retry policy for retryable errors inside the adapter (Section 6.5).
RETRY_BACKOFF_MS = (200, 800)
MAX_RETRIES = 2
IDEMPOTENCY_TTL_HOURS = 24
