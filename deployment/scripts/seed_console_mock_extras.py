#!/usr/bin/env python3
"""Add the Case Detail attributes in opsresolve.console_api.mock_extras to EXISTING mock items.

Attribute-level put-if-absent only: for each item it GetItems, then UpdateItem SETs just the
missing attributes with ``attribute_exists(pk) AND attribute_not_exists(<each attr>)``. It never
creates an item, never overwrites or removes an attribute, never deletes, never changes schema.
Idempotent: a second run reports every item "already complete". Never prints contact details.

Usage (from tcs-agentic-opsresolve/):
  python scripts/seed_console_mock_extras.py --dry-run   # read-only GetItems, print the plan
  python scripts/seed_console_mock_extras.py             # WRITES to AWS (asks for confirmation)
  python scripts/seed_console_mock_extras.py --yes       # skip confirmation
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from opsresolve.common.config import settings  # noqa: E402
from opsresolve.console_api.mock_extras import MOCK_EXTRAS, MOCK_SK, missing_attributes  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="read-only: print what would be added")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    a = p.parse_args()

    import boto3
    from botocore.exceptions import ClientError

    ddb = boto3.resource("dynamodb", region_name=settings.region)
    plan: list[tuple[str, str, dict]] = []
    skipped = 0
    for table, items in MOCK_EXTRAS.items():
        t = ddb.Table(table)
        for pk, wanted in items.items():
            existing = t.get_item(Key={"pk": pk, "sk": MOCK_SK}, ConsistentRead=True).get("Item")
            if existing is None:
                print(f"SKIP (item missing)  {table} {pk}")
                skipped += 1
                continue
            add = missing_attributes(existing, wanted)
            if add:
                print(f"{table} {pk} +{sorted(add)}")
                plan.append((table, pk, add))
            else:
                print(f"already complete     {table} {pk}")
    print(f"{len(plan)} item(s) to update, {skipped} skipped")
    if a.dry_run or not plan:
        print("Dry run: nothing written." if a.dry_run else "Nothing to do.")
        return 0
    if not a.yes and input("Add these attributes (existing values are never changed)? Type 'yes': ") != "yes":
        print("Aborted.")
        return 1
    for table, pk, add in plan:
        names = {f"#a{i}": k for i, k in enumerate(add)}
        values = {f":v{i}": v for i, v in enumerate(add.values())}
        cond = " AND ".join(["attribute_exists(pk)"] + [f"attribute_not_exists({n})" for n in names])
        try:
            ddb.Table(table).update_item(
                Key={"pk": pk, "sk": MOCK_SK},
                UpdateExpression="SET " + ", ".join(f"{n} = :v{i}" for i, n in enumerate(names)),
                ConditionExpression=cond,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
            print(f"updated  {table} {pk} +{sorted(add)}")
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            print(f"CONDITION FAILED (left unchanged)  {table} {pk}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
