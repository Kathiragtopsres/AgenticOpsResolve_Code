"""Thin DynamoDB access helpers shared by tool adapters (Section 6, Section 9.1/9.2).

Every mock-data table (OPS-003) uses a single-item-per-entity shape: ``pk`` =
``"<ENTITY>#<id>"``, ``sk`` = ``"META"`` (``mock-inventory`` is the one exception:
``pk`` = ``"INV#<depotId>#<partId>"``). The telemetry table uses ``pk`` =
``"TELEMETRY#<assetId>#<profile>"``, ``sk`` = the ISO-8601 timestamp.

Kept import-safe without a live AWS session: the boto3 resource is created lazily so
this module (and anything importing it) can still be imported for unit tests that stub
``get_table``.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from .config import settings


@lru_cache(maxsize=1)
def _resource():  # pragma: no cover - thin AWS SDK wrapper
    import boto3

    return boto3.resource("dynamodb", region_name=settings.region)


def get_table(table_name: str):  # pragma: no cover - thin AWS SDK wrapper
    return _resource().Table(table_name)


def get_item(table_name: str, pk: str, sk: str = "META") -> dict[str, Any] | None:
    """Fetch a single item by key; return ``None`` if it doesn't exist."""
    resp = get_table(table_name).get_item(Key={"pk": pk, "sk": sk})
    return resp.get("Item")


def query_by_pk_prefix(table_name: str, pk: str) -> list[dict[str, Any]]:
    """Query all items sharing an exact partition key (e.g. all timeline/plan/message
    entries for one case, or all telemetry samples for one asset+profile)."""
    from boto3.dynamodb.conditions import Key

    items: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"KeyConditionExpression": Key("pk").eq(pk)}
    while True:
        resp = get_table(table_name).query(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items


def scan_table(table_name: str, filter_fn=None) -> list[dict[str, Any]]:
    """Full scan of a small reference table (mock tables have <=20 items each), with an
    optional client-side filter. Fine at this scale; do not use on the case store."""
    items: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {}
    while True:
        resp = get_table(table_name).scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return [i for i in items if filter_fn is None or filter_fn(i)] if filter_fn else items
