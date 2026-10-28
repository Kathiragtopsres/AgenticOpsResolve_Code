"""Part-shortage recovery data for tenants that offer a courier self-install (``recovery.courierSelfInstall``).

When the part booked for a visit cannot be picked, the customer chooses between a courier delivery with
self-install and a technician visit tomorrow. The courier option exists only if the case's part is
customer-replaceable, the customer's contract covers courier replacement and a courier hub in the customer's
region has the part in stock. Any lookup problem means "not offered" (the planner then escalates).
"""
from __future__ import annotations

from typing import Any

from ..common import dynamo
from ..common.config import settings
from ..common.logging import get_logger

logger = get_logger(__name__)

COURIER = "COURIER"
TECH_TOMORROW = "TECH_TOMORROW"
# Intent of the two offer buttons; the optionId says which one was tapped.
RECOVERY_CHOICE = "RECOVERY_CHOICE"
SELF_INSTALL_DONE = "SELF_INSTALL_DONE"


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def courier_plan(case_id: str) -> dict | None:
    """``{"partId", "courierDepotId", "heldReservationId", "heldDepotId"}`` for a courier self-install of the
    case's required part, or None when it cannot be offered. The held reservation is the open (failed) hold
    on the part, if any; the caller releases it."""
    try:
        meta = dynamo.get_item(settings.cases_table, f"CASE#{case_id}") or {}
        part_id = (meta.get("requiredPart") or {}).get("partId")
        customer_id, asset_id = meta.get("customerId"), meta.get("assetId")
        if not (part_id and customer_id and asset_id):
            return None
        part = dynamo.get_item(settings.mock_parts_table, f"PARTS#{part_id}") or {}
        if part.get("customerReplaceable") is not True:
            return None
        contracts = dynamo.scan_table(
            settings.mock_contracts_table,
            filter_fn=lambda c: c.get("customerId") == customer_id and asset_id in (c.get("assetIds") or []),
        )
        if not contracts or (contracts[0].get("coverage") or {}).get("courierReplacement") is not True:
            return None
        customer = dynamo.get_item(settings.mock_customers_table, f"CUSTOMERS#{customer_id}") or {}
        hubs = dynamo.scan_table(
            settings.mock_depots_table,
            filter_fn=lambda d: d.get("courier") is True and d.get("region") == customer.get("region"),
        )
        for hub in sorted(hubs, key=lambda d: str(d.get("depotId"))):
            stock = dynamo.get_item(settings.mock_inventory_table, f"INV#{hub['depotId']}#{part_id}") or {}
            if _number(stock.get("onHand")) - _number(stock.get("reserved")) < 1:
                continue
            held = sorted(
                dynamo.scan_table(
                    f"{settings.prefix}-reservations",
                    filter_fn=lambda r: r.get("caseId") == case_id and r.get("status") == "HELD",
                ),
                key=lambda r: str(r.get("createdAt")),
            )
            latest = held[-1] if held else {}
            return {
                "partId": part_id, "courierDepotId": hub["depotId"],
                "heldReservationId": latest.get("reservationId"), "heldDepotId": latest.get("depotId"),
            }
    except Exception as exc:  # fail closed: no courier option
        logger.error(f"Courier plan lookup failed for case {case_id}: {exc}")
    return None
