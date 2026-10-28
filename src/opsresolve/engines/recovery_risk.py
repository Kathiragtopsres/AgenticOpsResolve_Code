"""Service recovery risk engine (Section 8.5).

Deterministic. Runs on every ETA checkpoint timer (T-90, T-45, T-15) and on relevant WFM/ERP
events. Computes breach probability and scores recovery options. Kept identical to the
reference oracle (reference/opsresolve_ref/engines.py).
"""
from __future__ import annotations

from datetime import datetime, timedelta

RECOVERY_OPTIONS = (
    "ALT_TECHNICIAN",
    "LATER_SLOT_WITHIN_SLA",
    "PART_TRANSFER",
    "REMOTE_INTERIM_ACTION",
    "ESCALATE",
)


def risk(
    now: datetime,
    remaining_job_min: int,
    travel_min: int,
    slot_start: datetime,
    restore_by: datetime,
    est_repair_min: int,
    tolerance_min: int = 15,
) -> dict:
    predicted_arrival = now + timedelta(minutes=remaining_job_min + travel_min)
    slack = (slot_start + timedelta(minutes=tolerance_min) - predicted_arrival).total_seconds() / 60
    sla_slack = (restore_by - (predicted_arrival + timedelta(minutes=est_repair_min))).total_seconds() / 60
    if sla_slack < 0 or slack < -30:
        level = "HIGH"
    elif slack < 0:
        level = "MEDIUM"
    else:
        level = "LOW"
    return {
        "predictedArrival": predicted_arrival.isoformat(),
        "slackMin": round(slack),
        "slaSlackMin": round(sla_slack),
        "risk": level,
    }


def option_score(
    sla_protected: bool,
    customer_effort: float,
    normalised_cost: float,
    ftf_likelihood: float,
) -> float:
    return round(
        0.45 * (1.0 if sla_protected else 0.0)
        + 0.25 * (1 - customer_effort)
        + 0.20 * (1 - normalised_cost)
        + 0.10 * ftf_likelihood,
        4,
    )
