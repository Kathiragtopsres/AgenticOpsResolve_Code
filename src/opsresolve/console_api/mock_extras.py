"""Additive attributes for existing mock-* items that the Case Detail screen joins on.

Pure data, no AWS calls. Applied by ``scripts/seed_console_mock_extras.py`` as attribute-level
put-if-absent updates (``attribute_exists(pk) AND attribute_not_exists(<attr>)``): an existing
attribute is never overwritten, a missing item is never created, and nothing is deleted.
Not imported by any Lambda; ``POST /scenario/reset`` never touches mock-* tables.

Layout: table name -> pk -> attributes to add (sk is always ``META``).
"""
from __future__ import annotations

from typing import Any

from ..common.config import settings

MOCK_SK = "META"

# pk prefix convention per table (matches tools/context.py lookups).
PK_PREFIX: dict[str, str] = {
    settings.mock_contacts_table: "CONTACTS#",
    settings.mock_sites_table: "SITES#",
    settings.mock_assets_table: "ASSETS#",
    settings.mock_contracts_table: "CONTRACTS#",
}

_CHILLER = "CX-500 cold-chain chiller"

MOCK_EXTRAS: dict[str, dict[str, dict[str, Any]]] = {
    settings.mock_contacts_table: {
        # CON-3107/3108 have no customerId, so a customerId join could not find them.
        "CONTACTS#CON-3107": {"customerId": "CUST-3107"},
        "CONTACTS#CON-3108": {"customerId": "CUST-3107"},
        "CONTACTS#CON-2201": {"role": "Account holder"},
    },
    settings.mock_sites_table: {
        "SITES#RAD-01": {"address": "Block C, St. Aurelia General Hospital, 18 Hospital Road"},
        "SITES#PREM-2201": {"name": "Nair Design Studio (home office)"},
    },
    settings.mock_assets_table: {
        "ASSETS#CHILLER-8821": {"displayName": _CHILLER, "lastServiceDate": "2025-12-07"},
        "ASSETS#CHILLER-8822": {"displayName": _CHILLER, "lastServiceDate": "2025-11-04"},
        "ASSETS#AHU-9011": {"displayName": "AHU-90 air handling unit", "lastServiceDate": "2025-07-19"},
        "ASSETS#MRI-4410": {"displayName": "MRX-3T MRI scanner", "installDate": "2020-08-21",
                            "lastServiceDate": "2026-06-15"},
        "ASSETS#ONT-55012": {"displayName": "ONT-X200 fibre ONT", "installDate": "2024-02-10",
                             "lastServiceDate": "2024-02-10"},
        "ASSETS#RTR-55013": {"displayName": "HGW-7 home gateway", "installDate": "2024-02-10",
                             "lastServiceDate": "2024-02-10"},
    },
    settings.mock_contracts_table: {
        "CONTRACTS#CTR-5510": {"planName": "Gold 4-hour restore"},
        "CONTRACTS#CTR-UPT-3107": {"planName": "Premium Uptime (imaging)", "endDate": "2027-06-30",
                                   "assetIds": ["MRI-4410"]},
        "CONTRACTS#PLAN-FIBRE-500-BIZ": {"planName": "Fibre 500 Business", "endDate": "2027-01-31",
                                         "assetIds": ["ONT-55012", "RTR-55013"]},
    },
}


def missing_attributes(existing: dict | None, wanted: dict) -> dict:
    """The subset of ``wanted`` whose keys are absent from ``existing``.

    Never returns a key the item already has (whatever its value), and returns ``{}`` when the
    item itself is missing, so callers can never create or overwrite mock data.
    """
    if existing is None:
        return {}
    return {k: v for k, v in wanted.items() if k not in existing}
