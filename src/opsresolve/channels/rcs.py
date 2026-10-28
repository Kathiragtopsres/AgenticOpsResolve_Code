"""RCS channel sender (Section 7.2).

Wraps AWS End User Messaging SendRcsMessage for rich cards, carousels, file messages and
suggestions, with per-message SMS fallback configured. SendTextMessage is used only for
plain text. Postback data is signed (inbound.postback) and routed on postbackData, never
on label text.

Construction rules (Section 7.2.2): carousel = 3 cards for slot choice; <= 3 message-level
suggestions; label <= 25 chars; PostbackData <= 2048 chars; TTL on recovery offers.
"""
from __future__ import annotations


def send_rcs_message(recipient: str, content: dict, fallback_sms: str | None = None) -> dict:  # pragma: no cover
    raise NotImplementedError


def build_slot_carousel(case_id: str, slots: list[dict]) -> dict:  # pragma: no cover
    """Build an RcsMessageContent carousel of up to 3 appointment slots (Listing 7-2)."""
    raise NotImplementedError
