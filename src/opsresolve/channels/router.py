"""Channel Router (Technical Specification Section 7.6).

Production implementation. Kept behaviourally identical to the reference oracle
(reference/opsresolve_ref/router.py) so contract tests can compare the two.

``select_channel`` returns an ordered list of channels to send on. A P1 URGENT_ALERT
returns a dual send [RCS, SMS]; APPROVAL_REQUEST always goes to SMS; REPORT prefers EMAIL
then RCS. Otherwise it honours the active WhatsApp window, then the preferred channel, then
the default order — always filtered by consent, capability and channel health.
"""
from __future__ import annotations

ORDER = ["RCS", "WHATSAPP", "SMS", "EMAIL"]

CAPABLE: dict[str, set[str]] = {
    "APPOINTMENT_OPTIONS": {"RCS", "WHATSAPP", "SMS"},
    "RECOVERY_OFFER": {"RCS", "WHATSAPP", "SMS"},
    "REPORT": {"EMAIL", "RCS"},
    "CSAT": {"RCS", "WHATSAPP", "SMS"},
    "ETA_UPDATE": {"RCS", "WHATSAPP", "SMS"},
    "CONFIRMATION": {"RCS", "WHATSAPP", "SMS", "EMAIL"},
    "CLARIFY": {"WHATSAPP", "RCS", "SMS"},
    "URGENT_ALERT": {"RCS", "SMS", "WHATSAPP"},
    "APPROVAL_REQUEST": {"SMS"},
}


def select_channel(
    intent: str,
    consent: dict,
    preferred: str,
    active_channel: str | None = None,
    wa_window_open: bool = False,
    priority: str = "P3",
    unhealthy: set | None = None,
) -> list[str]:
    unhealthy = unhealthy or set()
    ok = {c for c, v in consent.items() if v == "OPTED_IN" and c not in unhealthy}
    capable = CAPABLE.get(intent, set(ORDER))

    if intent == "APPROVAL_REQUEST":
        return ["SMS"]
    if intent == "REPORT":
        for c in ["EMAIL", "RCS"]:
            if c in ok:
                return [c]
        return []
    if intent == "URGENT_ALERT" and priority == "P1":
        return [c for c in ["RCS", "SMS"] if c in ok]
    if active_channel == "WHATSAPP" and wa_window_open and "WHATSAPP" in ok and "WHATSAPP" in capable:
        return ["WHATSAPP"]
    if preferred in ok and preferred in capable:
        return [preferred]
    for c in ORDER:
        if c in ok and c in capable:
            return [c]
    return []
