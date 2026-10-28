"""Technician Matching Agent (Section 5.4.7).

Ranks qualified technicians. Hard filters: required skill, valid certification for the
asset model, service region, availability. Soft score combines ETA, skill level,
historical first-time-fix rate and utilisation balance. Selection binds to the slot the
customer chooses.
"""
from __future__ import annotations

from .base import SpecialistSpec
from ..common.enums import AutonomyLevel

SPEC = SpecialistSpec(
    name="TechnicianMatchingAgent",
    purpose="Rank qualified technicians for a required skill, model and region.",
    tools=["findQualifiedTechnicians"],
    autonomy=AutonomyLevel.L1,
    prompt_file="technician_matching.v2.md",
)

# Soft-score weights (Section 5.4.7). Delegated to engines.technician_scoring.
SCORE_WEIGHTS = {
    "eta": 0.40,
    "skill_level": 0.25,
    "first_time_fix": 0.20,
    "utilisation": 0.15,
}
