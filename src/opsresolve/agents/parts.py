"""Parts Agent (Section 5.4.6).

Scope the likely part(s), check availability across depots, hold/reserve, reroute on
shortage. Reservation created as HOLD (TTL 2h), confirmed only when the appointment is
booked. Autonomous L2 when unit value <= threshold; otherwise NEEDS_APPROVAL (L4).
"""
from __future__ import annotations

from .base import SpecialistSpec
from ..common.enums import AutonomyLevel

SPEC = SpecialistSpec(
    name="PartsAgent",
    purpose="Scope, locate, hold/reserve and reroute parts.",
    tools=["findAndReservePart", "releasePartReservation", "transferPart", "dispatchCourier"],
    autonomy=AutonomyLevel.L2,
    prompt_file="parts.v2.md",
)

DEFAULT_VALUE_THRESHOLD_USD = 500.0
HOLD_TTL_HOURS = 2
