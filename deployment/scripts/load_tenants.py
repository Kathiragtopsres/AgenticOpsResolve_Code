#!/usr/bin/env python3
"""Load seed/tenants.json into the tenants table (pk/sk items, written as-is).

Usage: python scripts/load_tenants.py [--env dev] [--dry-run]
Table: opsresolve-<env>-tenants. ACTIVE_DEMO is not seeded (written by console Reset).
"""
import argparse, json, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
ap = argparse.ArgumentParser(); ap.add_argument("--env", default="dev"); ap.add_argument("--dry-run", action="store_true")
a = ap.parse_args()
table = f"opsresolve-{a.env}-tenants"
items = json.loads((ROOT / "seed" / "tenants.json").read_text(encoding="utf-8"), parse_float=__import__("decimal").Decimal)
tbl = None
if not a.dry_run:
    import boto3; tbl = boto3.resource("dynamodb", region_name="us-east-1").Table(table)
for it in items:
    if a.dry_run: print(table, it["pk"], it["sk"])
    else: tbl.put_item(Item=it)
print(f"{'Would load' if a.dry_run else 'Loaded'} {len(items)} items into {table}")
