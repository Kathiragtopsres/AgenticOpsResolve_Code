#!/usr/bin/env python3
"""Load a scenario's seed data into the mock-system DynamoDB tables.

Usage: python scripts/load_seed.py --scenario s1-cold-chain-chiller --env demo [--dry-run]
Table naming: opsresolve-<env>-mock-<entity>, partition key 'pk' = '<ENTITY>#<id>', sort key 'sk' = 'META'.
"""
import argparse, json, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
KEYS = {"customers": "customerId", "sites": "siteId", "contacts": "contactId", "assets": "assetId", "contracts": "contractId",
        "depots": "depotId", "parts": "partId", "technicians": "technicianId", "work_order_history": "workOrderId",
        "approvers": "approverId", "couriers": "courierId"}
ap = argparse.ArgumentParser(); ap.add_argument("--scenario", required=True); ap.add_argument("--env", default="demo"); ap.add_argument("--dry-run", action="store_true")
ap.add_argument("--only", nargs="+", help="entity file names to load (e.g. parts work_order_history); default all")
a = ap.parse_args()
ddb = None
if not a.dry_run:
    import boto3; ddb = boto3.resource("dynamodb")
count = 0
for f in sorted((ROOT / "seed" / a.scenario).glob("*.json")):
    entity = f.stem
    if a.only and entity not in a.only: continue
    data = json.loads(f.read_text())
    items = data if isinstance(data, list) else [data]
    table = f"opsresolve-{a.env}-mock-{entity.replace('_', '-')}"
    for it in items:
        key = KEYS.get(entity)
        pk = f"{entity.upper()}#{it[key]}" if key and key in it else (f"INV#{it['depotId']}#{it['partId']}" if entity == "inventory" else f"{entity.upper()}#ALL")
        item = {"pk": pk, "sk": "META", **json.loads(json.dumps(it), parse_float=str)}
        if a.dry_run: print(table, pk)
        else: ddb.Table(table).put_item(Item=item)
        count += 1
print(f"{'Would load' if a.dry_run else 'Loaded'} {count} items for {a.scenario}")
