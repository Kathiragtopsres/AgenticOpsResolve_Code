"""SLA and Entitlement Agent (Section 5.4.5).

Determines coverage, SLA clock, priority and escalation policy. Rules evaluate coverage;
the LLM only generates the plain-language coverage explanation. Warranty denial is L4.
"""
from __future__ import annotations

from .base import SpecialistSpec
from ..common.enums import AutonomyLevel

SPEC = SpecialistSpec(
    name="SlaEntitlementAgent",
    purpose="Determine coverage, SLA clock, priority and escalation policy.",
    tools=["checkServiceEntitlement"],
    autonomy=AutonomyLevel.L0,  # warranty denial is always L4
    prompt_file="entitlement.v2.md",
)
