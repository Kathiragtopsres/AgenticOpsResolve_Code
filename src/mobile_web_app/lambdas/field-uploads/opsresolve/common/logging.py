"""Structured logging helpers with PII masking (Sections 11, 12).

No PII beyond masked phone/email may appear in logs (NFR-08). Use ``mask_phone``
and ``mask_email`` before logging any contact identifiers.
"""
from __future__ import annotations

import logging
import re
from decimal import Decimal
from typing import Any

_PHONE_RE = re.compile(r"(\+?\d[\d\-\s]{5,})(\d{4})")
_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+)")


def mask_phone(value: str) -> str:
    """Keep only the last 4 digits: '+1 555-0100' -> '******0100'."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) <= 4:
        return "*" * len(digits)
    return "*" * (len(digits) - 4) + digits[-4:]


def mask_email(value: str) -> str:
    return _EMAIL_RE.sub(r"\1***\2", value or "")


_SECRET_KEY_RE = re.compile(r"token|secret|password|authorization|api[-_]?key|nonce|hmac|signature|postback", re.I)
_PHONE_KEY_RE = re.compile(r"phone|msisdn|mobile", re.I)
_EMAIL_KEY_RE = re.compile(r"email", re.I)
_PHONE_IN_TEXT_RE = re.compile(r"\+?\d[\d\-\s()]{7,}\d")
_BEARER_RE = re.compile(r"(?i)\b(bearer|eyJ[\w-]{10,})[\w.\-]*")


_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _mask_phone_match(m: re.Match) -> str:
    text = m.group(0)
    return text if _ISO_DATE_RE.fullmatch(text) else mask_phone(text)  # ISO dates are not phone numbers


def _mask_text(value: str) -> str:
    value = _EMAIL_RE.sub(r"\1***\2", value)
    value = _BEARER_RE.sub("***", value)
    return _PHONE_IN_TEXT_RE.sub(_mask_phone_match, value)


def mask_payload(value: Any, *, max_str: int = 500, max_items: int = 20, _depth: int = 0) -> Any:
    """Mask + truncate an arbitrary JSON-ish payload for timeline/trace storage.

    Key-based: values under token/secret/nonce/... keys become '***', phone/email keys
    are masked. Text-based: emails and phone-like numbers inside strings are masked.
    Strings longer than ``max_str`` and lists/dicts beyond ``max_items`` are truncated.
    """
    if _depth > 6:
        return "...(max depth)"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= max_items:
                out["..."] = f"{len(value) - max_items} more keys truncated"
                break
            key = str(k)
            if _SECRET_KEY_RE.search(key):
                out[key] = "***"
            elif _PHONE_KEY_RE.search(key) and isinstance(v, str):
                out[key] = mask_phone(v)
            elif _EMAIL_KEY_RE.search(key) and isinstance(v, str):
                out[key] = mask_email(v)
            else:
                out[key] = mask_payload(v, max_str=max_str, max_items=max_items, _depth=_depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        items = [mask_payload(v, max_str=max_str, max_items=max_items, _depth=_depth + 1) for v in value[:max_items]]
        if len(value) > max_items:
            items.append(f"...{len(value) - max_items} more items truncated")
        return items
    if isinstance(value, str):
        text = _mask_text(value)
        return text if len(text) <= max_str else text[:max_str] + f"...(+{len(text) - max_str} chars)"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Decimal):  # DynamoDB numbers: keep numeric so JSON consumers get numbers
        return int(value) if value == value.to_integral_value() else float(value)
    return mask_payload(str(value), max_str=max_str, max_items=max_items, _depth=_depth + 1)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter('{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
