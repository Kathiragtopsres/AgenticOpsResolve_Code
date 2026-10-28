"""Workforce domain tools (Section 6.2).

  findQualifiedTechnicians (Read, Mock WFM) — delegates ranking to engines.technician_scoring
  getTechnicianStatus      (Read, Mock WFM: location, job progress)
"""
from __future__ import annotations

from datetime import date

from ..common import dynamo
from ..common.config import settings
from ..common.enums import ToolErrorCode
from ..engines.technician_scoring import Technician, rank
from .base import error_envelope


def find_qualified_technicians(payload: dict) -> dict:
    """Rank qualified technicians for a required skill, asset model and region.

    Mirrors schemas/tools/findQualifiedTechnicians.schema.json. Hard filters and soft
    scoring are delegated to engines.technician_scoring (Section 5.4.7). Input:
    {requiredSkill, assetModel, region, regionsAllowed?, windowStart?, windowEnd?}.
    """
    required_skill = payload.get("requiredSkill")
    asset_model = payload.get("assetModel")
    region = payload.get("region")
    correlation_id = payload.get("correlationId", payload.get("caseId", ""))
    if not required_skill or not asset_model or not region:
        return error_envelope(
            ToolErrorCode.VALIDATION_ERROR,
            "requiredSkill, assetModel and region are required",
            correlation_id,
        )

    rows = dynamo.scan_table(settings.mock_technicians_table)
    techs = [_to_technician(row) for row in rows]

    on_date = payload.get("windowStart", date.today().isoformat())[:10]
    ranked_ids, rejected = rank(
        techs, required_skill, asset_model, region, on_date,
        regions_allowed=payload.get("regionsAllowed"),
    )

    by_id = {t.id: t for t in techs}
    candidates = [
        {"technicianId": tid, "score": _score_for(by_id[tid], required_skill, asset_model)}
        for tid in ranked_ids
    ]
    return {"candidates": candidates, "rejected": rejected}


def get_technician_status(payload: dict) -> dict:
    """Return a technician's current location/ETA and job-progress state.

    Input: {technicianId}.
    """
    technician_id = payload.get("technicianId")
    correlation_id = payload.get("correlationId", "")
    if not technician_id:
        return error_envelope(
            ToolErrorCode.VALIDATION_ERROR, "technicianId is required", correlation_id
        )
    row = dynamo.get_item(settings.mock_technicians_table, f"TECHNICIANS#{technician_id}")
    if row is None:
        return error_envelope(
            ToolErrorCode.NOT_FOUND, f"Unknown technicianId {technician_id}", correlation_id
        )
    return {
        "technicianId": technician_id,
        "region": row.get("region"),
        "utilisation": row.get("utilisation"),
        "availableFrom": row.get("availableFrom"),
        "nextFreeSlot": row.get("nextFreeSlot"),
        "etaMinToSite": row.get("etaMinAtRecovery") or row.get("etaMinToPlantA"),
    }


def _to_technician(row: dict) -> Technician:
    skills = {k: int(v) for k, v in row.get("skills", {}).items()}
    ftf_rate = {k: float(v) for k, v in row.get("ftfRate", {}).items()}
    eta_min = int(row.get("etaMinAtRecovery") or row.get("etaMinToPlantA") or 999)
    return Technician(
        id=row["technicianId"],
        name=row.get("name", ""),
        skills=skills,
        certifications=row.get("certifications", {}),
        region=row.get("region", ""),
        eta_min=eta_min,
        ftf_rate=ftf_rate,
        utilisation=float(row.get("utilisation", 0)),
    )


def _score_for(t: Technician, skill: str, model: str) -> float:
    from ..engines.technician_scoring import score

    return score(t, skill, model)
