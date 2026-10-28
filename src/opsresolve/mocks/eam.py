"""Mock EAM / Asset registry (Section 6.4).

Seeded: assets (CX-500, CX-300, AHU-90, MRX imaging) and historical work orders.
Backs getAssetContext (asset) and getServiceHistory.
Production adapters: IBM Maximo, SAP PM, Infor EAM.
"""
from __future__ import annotations


def get_asset(asset_id: str) -> dict:  # pragma: no cover
    raise NotImplementedError


def get_service_history(asset_id: str) -> list[dict]:  # pragma: no cover
    raise NotImplementedError
