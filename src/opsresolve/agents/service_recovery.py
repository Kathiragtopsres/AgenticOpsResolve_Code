"""Service Recovery Agent (Section 5.4.9).

Detects and resolves execution risk before the customer is affected. A deterministic risk
engine (engines.recovery_risk) computes breach probability and recovery options; the LLM
ranks options and drafts the customer explanation. L1 by default; L3 auto-reassign when the
alternate technician is same-cost, earlier, and the customer pre-consented.
"""
from __future__ import annotations

from .base import SpecialistSpec
from ..common.enums import AutonomyLevel

SPEC = SpecialistSpec(
    name="ServiceRecoveryAgent",
    purpose="Detect and resolve execution risk; generate and rank recovery options.",
    tools=[
        "getTechnicianStatus",
        "findQualifiedTechnicians",
        "reassignTechnician",
        "transferPart",
        "proposeAppointmentSlots",
        "dispatchCourier",
    ],
    autonomy=AutonomyLevel.L1,
    prompt_file="service_recovery.v3.md",
)

RECOVERY_OFFER_TTL_MINUTES = 30
