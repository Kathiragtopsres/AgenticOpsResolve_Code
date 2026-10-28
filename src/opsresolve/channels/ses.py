"""Email channel sender (Section 7.5).

Amazon SES v2 templated email for booking confirmations, service reports and escalation
notices (templates: opsr-booking-confirmation, opsr-service-report, opsr-escalation-notice).
The service report (HTML rendered to PDF by the report Lambda) is stored in S3 and linked via
a pre-signed URL (7-day expiry) rather than attached.
"""
from __future__ import annotations


def send_templated_email(recipient: str, template_name: str, template_data: dict) -> dict:  # pragma: no cover
    raise NotImplementedError
