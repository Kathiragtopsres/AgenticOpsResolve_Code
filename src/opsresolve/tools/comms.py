"""Communications domain tools (Section 6.2, Listing 6-3).

  sendCustomerMessage (Write, CDS Channel Router, idempotent per intent)
  requestApproval     (Write, Approvals table + SMS/console, idempotent)

sendCustomerMessage consults the Channel Router (channels.router.select_channel) and the
contact consent record; a non-consented channel returns CHANNEL_NOT_CONSENTED.
"""
from __future__ import annotations


def send_customer_message(payload: dict) -> dict:  # pragma: no cover
    raise NotImplementedError


def request_approval(payload: dict) -> dict:  # pragma: no cover
    raise NotImplementedError
