"""Appointment slot engine (Sections 5.4.8, 8.5).

Proposes feasible slots over WFM calendars: SLA-meeting slots first (by score), then
out-of-SLA options; supports blocked-hours windows (e.g. clinical hours for S3). Kept
identical to the reference oracle (reference/opsresolve_ref/engines.py).
"""
from __future__ import annotations

from datetime import datetime


def propose_slots(
    candidates: list[dict],
    restore_by: datetime,
    max_slots: int = 3,
    blocked_hours: tuple[int, int] | None = None,
) -> list[dict]:
    """Rank and trim candidate slots.

    candidates: [{slotId, start(datetime), end, technicianId, score, partReady}]
    """

    def allowed(c: dict) -> bool:
        if blocked_hours is None:
            return True
        h = c["start"].hour
        return not (blocked_hours[0] <= h < blocked_hours[1])

    pool = [
        dict(c, meetsSla=c["end"] <= restore_by)
        for c in candidates
        if allowed(c) and c.get("partReady", True)
    ]
    pool.sort(key=lambda c: (not c["meetsSla"], -c["score"], c["start"]))
    return pool[:max_slots]
