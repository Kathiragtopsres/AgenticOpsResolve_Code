"""Scheduling and Dispatch Agent (Section 5.4.8).

Computes feasible slots, books the appointment and dispatches. Offers at most 3 slots;
every slot must satisfy restoreBy unless the customer asks for later; booking only on a
verified postback or Flow submission (consent). L2 on customer consent.
"""
from __future__ import annotations

from .base import SpecialistSpec
from ..common.enums import AutonomyLevel

SPEC = SpecialistSpec(
    name="SchedulingDispatchAgent",
    purpose="Compute feasible slots, book the appointment and dispatch.",
    tools=["proposeAppointmentSlots", "createServiceAppointment", "updateWorkOrder"],
    autonomy=AutonomyLevel.L2,
    prompt_file="scheduling.v2.md",
)

MAX_SLOTS = 3

# Planner defaults for proposeAppointmentSlots (B4): one 2 h availability window, searched
# from now to now + SLOT_SEARCH_DAYS (UTC).
DEFAULT_JOB_DURATION_MIN = 120
SLOT_SEARCH_DAYS = 7
