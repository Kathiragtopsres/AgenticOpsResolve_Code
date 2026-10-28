#!/usr/bin/env python3
"""Read-only backup of the Ops Console demo data before a reseed.

Writes .agents/backups/<UTC YYYYMMDDTHHMMSSZ>/:
  opsresolve-dev-cases.json, opsresolve-dev-approvals.json, opsresolve-dev-conversations.json
      full paginated scans, DynamoDB-typed JSON (re-puttable with put-item / batch-write-item)
  mock-items.json
      GetItem of every mock item listed in console_api.mock_extras.MOCK_EXTRAS ({table: {pk: item|null}})
Re-reads each file and prints its item count. Never writes to AWS.

Usage (from tcs-agentic-opsresolve/):  python scripts/backup_console_demo.py
"""
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))

from opsresolve.common.config import settings  # noqa: E402
from opsresolve.console_api.mock_extras import MOCK_EXTRAS, MOCK_SK  # noqa: E402


def main() -> int:
    import boto3

    client = boto3.client("dynamodb", region_name=settings.region)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = os.path.normpath(os.path.join(ROOT, ".agents", "backups", stamp))
    os.makedirs(out, exist_ok=True)

    for table in (settings.cases_table, settings.approvals_table, settings.conversations_table):
        items = []
        for page in client.get_paginator("scan").paginate(TableName=table, ConsistentRead=True):
            items.extend(page["Items"])
        with open(os.path.join(out, f"{table}.json"), "w", encoding="utf-8") as f:
            json.dump(items, f, indent=1, sort_keys=True)

    mocks: dict[str, dict] = {}
    for table, entries in MOCK_EXTRAS.items():
        mocks[table] = {}
        for pk in entries:
            resp = client.get_item(TableName=table, Key={"pk": {"S": pk}, "sk": {"S": MOCK_SK}}, ConsistentRead=True)
            mocks[table][pk] = resp.get("Item")
    with open(os.path.join(out, "mock-items.json"), "w", encoding="utf-8") as f:
        json.dump(mocks, f, indent=1, sort_keys=True)

    print(f"Backup folder: {out}")
    for name in sorted(os.listdir(out)):
        with open(os.path.join(out, name), encoding="utf-8") as f:
            data = json.load(f)
        n = sum(1 for t in data.values() for v in t.values() if v) if name == "mock-items.json" else len(data)
        print(f"  {name}: {n} items")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
