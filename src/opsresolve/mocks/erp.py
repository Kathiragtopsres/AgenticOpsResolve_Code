"""Mock ERP / Inventory (Section 6.4).

Seeded: part SKUs, depots with stock and transfer lead times. One depot carries a
``pickFailure`` flag used as the S2 recovery trigger. Backs findAndReservePart,
releasePartReservation and transferPart.
Production adapters: SAP S/4HANA, Oracle SCM.
"""
from __future__ import annotations


def find_part(fault: str, asset_id: str) -> dict:  # pragma: no cover
    raise NotImplementedError


def reserve(part_id: str, depot_id: str, mode: str) -> dict:  # pragma: no cover
    raise NotImplementedError


def transfer(part_id: str, from_depot: str, to_depot: str) -> dict:  # pragma: no cover
    raise NotImplementedError
