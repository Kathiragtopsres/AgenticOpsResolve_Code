"""Context domain tools (Section 6.2).

Read tools that assemble customer/site/asset/contract/health context.

  getAssetContext         (Read, Mock CRM + EAM)
  getServiceHistory       (Read, Mock EAM/FSM)
  getAssetHealth          (Read, Telemetry table, IoT-fed)
  getContactPreferences   (Read, conversations/contacts store)

Each function is the handler body for a Lambda adapter; wire the actual Lambda entry
point (event/context) in infra/ Tools stack. Backed by the mock-data tables (OPS-003)
and the telemetry table via common.dynamo — see schemas/tools/*.schema.json in
opsresolve_build_test_kit for the exact input/output contracts.
"""
from __future__ import annotations

from ..common import dynamo, tenants
from ..common.config import settings
from ..common.enums import ToolErrorCode
from .base import error_envelope


def get_asset_context(payload: dict) -> dict:
    """Assemble asset + site + customer + contacts + contract + recent work orders.

    Mirrors schemas/tools/getAssetContext.schema.json. Input: {assetId, caseId?}.
    """
    asset_id = payload.get("assetId")
    correlation_id = payload.get("correlationId", "")
    if not asset_id:
        return error_envelope(
            ToolErrorCode.VALIDATION_ERROR, "assetId is required", correlation_id
        )

    asset = dynamo.get_item(settings.mock_assets_table, f"ASSETS#{asset_id}")
    if asset is None:
        return error_envelope(
            ToolErrorCode.NOT_FOUND, f"Unknown assetId {asset_id}", correlation_id
        )

    site = None
    customer = None
    contract = None
    contacts: list[dict] = []

    site_id = asset.get("siteId")
    if site_id:
        site = dynamo.get_item(settings.mock_sites_table, f"SITES#{site_id}")

    customer_id = (site or {}).get("customerId")
    if customer_id:
        customer = dynamo.get_item(settings.mock_customers_table, f"CUSTOMERS#{customer_id}")
        contacts = dynamo.scan_table(
            settings.mock_contacts_table,
            filter_fn=lambda item: item.get("customerId") == customer_id,
        )
        contracts = dynamo.scan_table(
            settings.mock_contracts_table,
            filter_fn=lambda item: item.get("customerId") == customer_id
            and asset_id in item.get("assetIds", []),
        )
        contract = contracts[0] if contracts else None

    recent_work_orders = dynamo.scan_table(
        settings.mock_work_order_history_table,
        filter_fn=lambda item: item.get("assetId") == asset_id,
    )

    return {
        "asset": _strip_keys(asset),
        "site": _strip_keys(site) if site else None,
        "customer": _strip_keys(customer) if customer else None,
        "contacts": [_strip_keys(c) for c in contacts],
        "contract": _strip_keys(contract) if contract else None,
        "recentWorkOrders": [_strip_keys(w) for w in recent_work_orders],
    }


def get_service_history(payload: dict) -> dict:
    """Return prior work orders for an asset, most recent first.

    Mirrors the recentWorkOrders shape used by getAssetContext. Input: {assetId}.
    """
    asset_id = payload.get("assetId")
    correlation_id = payload.get("correlationId", "")
    if not asset_id:
        return error_envelope(
            ToolErrorCode.VALIDATION_ERROR, "assetId is required", correlation_id
        )
    work_orders = dynamo.scan_table(
        settings.mock_work_order_history_table,
        filter_fn=lambda item: item.get("assetId") == asset_id,
    )
    work_orders.sort(key=lambda w: w.get("openedAt", ""), reverse=True)
    return {"assetId": asset_id, "workOrders": [_strip_keys(w) for w in work_orders]}


def get_asset_health(payload: dict) -> dict:
    """Return latest telemetry sample, trend and normal-band status for an asset.

    Mirrors schemas/tools/getAssetHealth.schema.json. Input: {assetId, windowMin?}.
    The telemetry table's pk is TELEMETRY#<assetId>#<profile>; the active profile is
    resolved from the asset's mock-assets record (falls back to scanning if absent).
    """
    asset_id = payload.get("assetId")
    correlation_id = payload.get("correlationId", "")
    if not asset_id:
        return error_envelope(
            ToolErrorCode.VALIDATION_ERROR, "assetId is required", correlation_id
        )

    asset = dynamo.get_item(settings.mock_assets_table, f"ASSETS#{asset_id}") or {}
    site = dynamo.get_item(settings.mock_sites_table, f"SITES#{asset['siteId']}") if asset.get("siteId") else None
    customer_id = (site or {}).get("customerId")
    tenant_id = tenants.tenant_for_customer(customer_id)
    profile = tenants.model_profile(tenant_id, asset.get("model")) or {}
    rules = profile.get("healthRules") or {}
    if profile.get("healthSource") == "NETWORK":
        return _network_health(asset_id, customer_id, rules, correlation_id)

    window_min = payload.get("windowMin", 60)
    samples = dynamo.scan_table(
        settings.telemetry_table, filter_fn=lambda item: item.get("assetId") == asset_id
    )
    if not samples:
        return error_envelope(
            ToolErrorCode.NOT_FOUND, f"No telemetry for assetId {asset_id}", correlation_id
        )

    samples.sort(key=lambda s: s.get("ts", ""))
    latest = samples[-1]
    window_samples = samples[-max(1, window_min // 5) :]

    # Breach thresholds from the model profile; default = S1 CX-500 (Technical Specification Section 9.4).
    pressure_max = float(rules.get("compressorPressureVarMax", 0.18))
    temp_max = float(rules.get("supplyTempCMax", 9.0))
    compressor_pressure_var = float(latest.get("compressorPressureVar", 0))
    supply_temp_c = float(latest.get("supplyTempC", 0))
    within_normal = compressor_pressure_var <= pressure_max and supply_temp_c <= temp_max

    trend = "STABLE"
    if len(window_samples) >= 2:
        delta = float(window_samples[-1].get("compressorPressureVar", 0)) - float(
            window_samples[0].get("compressorPressureVar", 0)
        )
        if delta > 0.02:
            trend = "DEGRADING"
        elif delta < -0.02:
            trend = "IMPROVING"

    alarms = []
    if compressor_pressure_var > pressure_max:
        alarms.append("CMP_CTRL_PV")
    if supply_temp_c > temp_max:
        alarms.append("SUPPLY_TEMP_HIGH")

    return {
        "assetId": asset_id,
        "latest": _strip_keys(latest),
        "trend": trend,
        "withinNormal": within_normal,
        "alarms": alarms,
    }


def _network_health(asset_id: str, customer_id: str | None, rules: dict, correlation_id: str) -> dict:
    """Health of a broadband asset from the active line profile in mock-network (S2).
    Same shape as the telemetry path plus ``syncState`` and ``areaOutage``. The mock loader stores
    numbers as strings, hence the float()s."""
    net = dynamo.get_item(settings.mock_network_table, "NETWORK#ALL") or {}
    entry = (net.get("lineProfiles") or {}).get(asset_id)
    if not entry:
        return error_envelope(
            ToolErrorCode.NOT_FOUND, f"No line diagnostics for assetId {asset_id}", correlation_id
        )
    latest = entry["profiles"][entry["active"]]
    sync_state = latest.get("syncState")
    alarms = []
    rx = latest.get("opticalRxDbm")
    if rx is not None and not float(rules.get("opticalRxDbmMin", -25)) <= float(rx) <= float(
        rules.get("opticalRxDbmMax", -8)
    ):
        alarms.append("OPTICAL_RX_OUT_OF_RANGE")
    if float(latest.get("fecErrorsPerMin", 0)) > float(rules.get("fecErrorsPerMinMax", 50)):
        alarms.append("FEC_ERRORS_HIGH")
    if float(latest.get("resetsLast24h", 0)) > float(rules.get("resetsLast24hMax", 2)):
        alarms.append("FREQUENT_RESETS")
    if sync_state != "UP":
        alarms.append("LINK_" + str(sync_state))
    region = (dynamo.get_item(settings.mock_customers_table, f"CUSTOMERS#{customer_id}") or {}).get("region")
    outages = [o.get("region") if isinstance(o, dict) else o for o in net.get("areaOutages") or []]
    return {
        "assetId": asset_id,
        "latest": latest,
        "trend": "STABLE",
        "withinNormal": not alarms,
        "alarms": alarms,
        "syncState": sync_state,
        "areaOutage": region in outages,
    }


def get_contact_preferences(payload: dict) -> dict:
    """Return a contact's preferred channel and consent record.

    Input: {contactId}.
    """
    contact_id = payload.get("contactId")
    correlation_id = payload.get("correlationId", "")
    if not contact_id:
        return error_envelope(
            ToolErrorCode.VALIDATION_ERROR, "contactId is required", correlation_id
        )
    contact = dynamo.get_item(settings.mock_contacts_table, f"CONTACTS#{contact_id}")
    if contact is None:
        return error_envelope(
            ToolErrorCode.NOT_FOUND, f"Unknown contactId {contact_id}", correlation_id
        )
    return {
        "contactId": contact_id,
        "preferredChannel": contact.get("preferredChannel"),
        "consent": contact.get("consent", {}),
    }


def _strip_keys(item: dict) -> dict:
    """Drop the raw pk/sk table keys before returning an item to a tool caller."""
    return {k: v for k, v in item.items() if k not in ("pk", "sk")}
