"""Signed postback data (Section 7.2.3).

Format: ``opsr1|<caseId>|<intent>|<optionId>|<expiresEpoch>|<nonce>|<hmac32>``
RCS PostbackData limit is 2,048 characters. The HMAC key is a demo key in the kit; in
production it lives in Secrets Manager and is rotated. Kept identical to the reference
oracle (reference/opsresolve_ref/postback.py).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

VERSION = "opsr1"
MAX_LEN = 2048


class PostbackError(ValueError):
    """Raised on malformed, tampered, expired or replayed postbacks."""


def _mac(key: bytes, payload: str) -> str:
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()[:32]


def sign(
    key: bytes,
    case_id: str,
    intent: str,
    option_id: str = "-",
    ttl_s: int = 1800,
    now: int | None = None,
    nonce: str | None = None,
) -> str:
    for part in (case_id, intent, option_id):
        if "|" in part:
            raise PostbackError("field may not contain '|'")
    now = int(time.time()) if now is None else now
    nonce = nonce or secrets.token_hex(4)
    payload = f"{VERSION}|{case_id}|{intent}|{option_id}|{now + ttl_s}|{nonce}"
    value = f"{payload}|{_mac(key, payload)}"
    if len(value) > MAX_LEN:
        raise PostbackError("postback exceeds 2048 characters")
    return value


def verify(key: bytes, value: str, now: int | None = None, consumed: set | None = None) -> dict:
    """Return the parsed postback or raise ``PostbackError``.

    ``consumed`` is the nonce store (a conditional-put set in production) enforcing
    single use.
    """
    parts = value.split("|")
    if len(parts) != 7 or parts[0] != VERSION:
        raise PostbackError("MALFORMED")
    _version, case_id, intent, option_id, exp, nonce, mac = parts
    payload = "|".join(parts[:6])
    if not hmac.compare_digest(mac, _mac(key, payload)):
        raise PostbackError("BAD_SIGNATURE")
    now = int(time.time()) if now is None else now
    if now > int(exp):
        raise PostbackError("EXPIRED")
    if consumed is not None:
        if nonce in consumed:
            raise PostbackError("REPLAYED")
        consumed.add(nonce)
    return {
        "caseId": case_id,
        "intent": intent,
        "optionId": option_id,
        "expires": int(exp),
        "nonce": nonce,
    }
