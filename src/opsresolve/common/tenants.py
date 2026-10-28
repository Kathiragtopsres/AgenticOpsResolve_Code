"""Tenant/scenario config from the tenants table (``opsresolve-<env>-tenants``).

Keys: ``TENANT#<id>``/``META`` | ``MODEL#<model>`` | ``PROMPT#<agent>``; ``CUSTOMER#<id>``/``META``
(customer -> tenant); ``ACTIVE_DEMO``/``META`` (tenant last Reset). A missing tenantId means
``DEFAULT_TENANT`` (live S1 items are not backfilled). If the table cannot be read,
``meravix-foods`` falls back to built-in S1 defaults so S1 never breaks; any other tenant raises.
"""
from __future__ import annotations

from typing import Any

from . import dynamo
from .config import settings
from .logging import get_logger

DEFAULT_TENANT = "meravix-foods"

_log = get_logger(__name__)
_cache: dict[tuple[str, str], dict[str, Any] | None] = {}
_FALLBACK_META: dict[str, Any] = {
    "tenantId": DEFAULT_TENANT,
    "displayName": "Meravix Foods",
    "scenario": "S1",
    "defaultRegion": "SOUTH",
    "caseOpening": {"customerMessageOpensCase": False},
    "demoAssetId": "CHILLER-8821",
    "demoContactId": "CON-77",
    "scenarioButtons": ["telemetry", "technician-delay", "part-shortage", "complete-work-order"],
    "telemetryProfiles": [{"id": i} for i in ("degrading", "post_reset_abnormal", "repaired", "normal")],
}


def reset_cache() -> None:
    _cache.clear()


def _item(pk: str, sk: str, tenant_id: str) -> dict[str, Any] | None:
    key = (pk, sk)
    if key not in _cache:
        try:
            _cache[key] = dynamo.get_item(settings.tenants_table, pk, sk)
        except Exception:
            if tenant_id != DEFAULT_TENANT:
                raise
            _log.warning("tenants table unreadable; using built-in S1 defaults (%s)", pk)
            return None  # not cached: retry next call
    return _cache[key]


def tenant(tenant_id: str | None) -> dict[str, Any]:
    tid = tenant_id or DEFAULT_TENANT
    item = _item(f"TENANT#{tid}", "META", tid)
    if item is None and tid == DEFAULT_TENANT:
        return dict(_FALLBACK_META)
    if item is None:
        raise LookupError(f"unknown tenant {tid}")
    return item


def model_profile(tenant_id: str | None, model: str | None) -> dict[str, Any] | None:
    if not model:
        return None
    tid = tenant_id or DEFAULT_TENANT
    return _item(f"TENANT#{tid}", f"MODEL#{model}", tid)


def prompt_rules(tenant_id: str | None, agent_name: str) -> str:
    tid = tenant_id or DEFAULT_TENANT
    item = _item(f"TENANT#{tid}", f"PROMPT#{agent_name}", tid)
    return (item or {}).get("rules", "")


def tenant_for_customer(customer_id: str | None) -> str:
    if not customer_id:
        return DEFAULT_TENANT
    item = _item(f"CUSTOMER#{customer_id}", "META", DEFAULT_TENANT)
    return (item or {}).get("tenantId") or DEFAULT_TENANT


def active_demo_tenant() -> str:
    item = _item("ACTIVE_DEMO", "META", DEFAULT_TENANT)
    return (item or {}).get("tenantId") or DEFAULT_TENANT


def set_active_demo_tenant(tenant_id: str) -> None:
    item = {"pk": "ACTIVE_DEMO", "sk": "META", "tenantId": tenant_id}
    dynamo.get_table(settings.tenants_table).put_item(Item=item)
    _cache.pop(("ACTIVE_DEMO", "META"), None)
