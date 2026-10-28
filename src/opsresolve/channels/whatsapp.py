"""WhatsApp channel sender (Section 7.4).

AWS End User Messaging Social (WABA). Utility templates: opsr_service_alert,
opsr_appointment_flow, opsr_eta_update, opsr_service_complete. Within the 24h service window,
free-form text and interactive reply/list messages are allowed. The "Book a service visit"
Dynamic Flow is defined as JSON and served by the Flow endpoint (inbound.flow_endpoint,
no LLM on path, p95 < 1.5s). WhatsApp is used only when consent WHATSAPP=OPTED_IN.
"""
from __future__ import annotations


def send_template_message(recipient: str, template_name: str, params: dict) -> dict:  # pragma: no cover
    raise NotImplementedError


def send_session_message(recipient: str, content: dict) -> dict:  # pragma: no cover
    raise NotImplementedError
