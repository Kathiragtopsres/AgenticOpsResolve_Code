"""SMS channel sender (Section 7.3).

AWS End User Messaging SendTextMessage over a two-way number. Used for urgent alerts, ETA
updates and manager approvals (reply "YES <code>" / "NO <code>"). Development uses the SMS
simulator destinations; the demo uses registered identities. Opt-out keywords are honoured
via the opt-out list and mirrored into the contact consent record.
"""
from __future__ import annotations


def send_text_message(recipient: str, body: str) -> dict:  # pragma: no cover
    raise NotImplementedError
