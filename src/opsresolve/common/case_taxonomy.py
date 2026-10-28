"""Deterministic case classification by asset model, applied when the agent opens a case.

``subcategory`` starts as the label below and is the *display* value: the DiagnosticAgent
replaces it with a fault-specific one once it has diagnosed (``subcategory`` on META).
``subcategoryTag`` is a copy of the label that is never overwritten, so cases can be filtered
on a stable value later. Queue names match the Ops Console seed (``console_api/scenario_seed.py``).
"""
from __future__ import annotations

from . import tenants

CASE_OWNER_AGENT = "System"

_Q_COLD = "Cold-Chain Field Service · South"
_Q_HVAC = "HVAC Field Service · South"
_Q_IMG = "Imaging Uptime · Clinical Engineering"
_Q_BB = "Broadband Home Support · South"

# asset model (mock-assets ``model``) -> category, subcategory label (= tag), assigned queue
TAXONOMY_BY_MODEL: dict[str, dict[str, str]] = {
    "CX-500": {"category": "Refrigeration", "subcategory": "Cold-chain chiller", "assignedQueue": _Q_COLD},
    "AHU-90": {"category": "HVAC", "subcategory": "Air handling unit", "assignedQueue": _Q_HVAC},
    "MRX-3T": {"category": "Medical Imaging", "subcategory": "MRI scanner", "assignedQueue": _Q_IMG},
    "ONT-X200": {"category": "Broadband", "subcategory": "Fibre ONT", "assignedQueue": _Q_BB},
    "HGW-7": {"category": "Broadband", "subcategory": "Home gateway", "assignedQueue": _Q_BB},
}


def classify(model: str | None, tenant_id: str | None = None) -> dict[str, str]:
    """META fields for an asset model: category, subcategory (display), subcategoryTag (filter),
    assignedQueue. The tenant's model profile wins; the built-in table is the fallback.
    Empty for an unknown model."""
    row = tenants.model_profile(tenant_id, model) or TAXONOMY_BY_MODEL.get(model or "")
    if not row:
        return {}
    return {
        "category": row["category"],
        "subcategory": row["subcategory"],
        "subcategoryTag": row["subcategory"],
        "assignedQueue": row["assignedQueue"],
    }
