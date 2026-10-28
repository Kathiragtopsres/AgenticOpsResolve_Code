"""Technician eligibility and soft-score ranking (Section 5.4.7).

Hard filters: required skill, valid certification for the asset model, service region,
availability. Soft score = 0.40*eta + 0.25*skill + 0.20*first_time_fix + 0.15*utilisation.
Kept identical to the reference oracle (reference/opsresolve_ref/engines.py).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Technician:
    id: str
    name: str
    skills: dict  # skill -> level 1..5
    certifications: dict  # model -> ISO expiry date
    region: str
    eta_min: int
    ftf_rate: dict  # model -> first-time-fix rate 0..1
    utilisation: float  # 0..1 today


def eligible(t: Technician, skill: str, model: str, region: str, on_date: str) -> tuple[bool, str]:
    if skill not in t.skills:
        return False, "SKILL_MISSING"
    exp = t.certifications.get(model)
    if exp is None:
        return False, "CERT_MISSING"
    if exp < on_date:
        return False, "CERT_EXPIRED"
    if t.region != region:
        return False, "OUT_OF_REGION"
    return True, "OK"


def score(t: Technician, skill: str, model: str, max_eta: int = 240) -> float:
    eta_score = max(0.0, 1 - t.eta_min / max_eta)
    skill_score = t.skills.get(skill, 0) / 5
    ftf = t.ftf_rate.get(model, 0.0)
    balance = 1 - t.utilisation
    return round(0.40 * eta_score + 0.25 * skill_score + 0.20 * ftf + 0.15 * balance, 4)


def rank(techs, skill, model, region, on_date, regions_allowed=None):
    regions_allowed = regions_allowed or [region]
    out, rejected = [], []
    for t in techs:
        ok, why = False, ""
        for r in regions_allowed:
            ok, why = eligible(t, skill, model, r, on_date)
            if ok:
                break
        (out if ok else rejected).append((t, why))
    ranked = sorted(((score(t, skill, model), t.id) for t, _ in out), reverse=True)
    return [tid for _, tid in ranked], {t.id: why for t, why in rejected}
