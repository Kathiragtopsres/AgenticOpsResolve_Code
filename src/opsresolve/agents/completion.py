"""Completion Agent (Section 5.4.11).

Verifies the outcome, structures the technician debrief, generates the report and closes
the case. Closes only if telemetry is within the normal band for 15 simulated minutes AND
(customer confirmation OR the 24h no-response rule); otherwise reopens to PLANNING_FIELD.
"""
from __future__ import annotations

from .base import SpecialistSpec
from ..common.enums import AutonomyLevel

SPEC = SpecialistSpec(
    name="CompletionAgent",
    purpose="Verify outcome, structure debrief, generate report and close the case.",
    tools=[
        "getAssetHealth",
        "updateWorkOrder",
        "generateServiceReport",
        "sendCustomerMessage",
        "applyServiceCredit",
        "closeServiceCase",
    ],
    autonomy=AutonomyLevel.L2,
    prompt_file="completion.v2.md",
)

VERIFY_NORMAL_MINUTES = 15
NO_RESPONSE_CLOSE_HOURS = 24
