"""Re-key a mock table from a hash-only key to pk/sk (``<PREFIX>#<id>`` / ``META``), keeping its rows.

Two steps around the delete / re-create that deploy.md runs by hand:

  python scripts/mock_table_rekey.py dump    --table mock-parts --key partId --out <dir>/mock-parts.items.json
  ... delete-table, create-table (pk/sk) ...
  python scripts/mock_table_rekey.py restore --table mock-parts --key partId --prefix PARTS --file <dir>/mock-parts.items.json

``dump`` is read-only. ``restore`` writes each dumped row back with ``pk = <prefix>#<row[key]>`` and ``sk = META``
added (typed DynamoDB JSON, so no value changes type) and refuses a table that is not pk/sk.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import boto3

REGION = "us-east-1"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["dump", "restore"])
    ap.add_argument("--table", required=True, help="e.g. mock-parts (opsresolve-dev- is added)")
    ap.add_argument("--key", required=True, help="attribute that identifies a row, e.g. partId")
    ap.add_argument("--prefix", help="pk prefix for restore, e.g. PARTS")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--file", type=Path)
    args = ap.parse_args()
    name = f"opsresolve-dev-{args.table}"
    client = boto3.client("dynamodb", region_name=REGION)

    if args.mode == "dump":
        items, kw = [], {"TableName": name}
        while True:
            resp = client.scan(**kw)
            items += resp["Items"]
            if "LastEvaluatedKey" not in resp:
                break
            kw["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(items, indent=2), encoding="utf-8")
        print(f"dumped {len(items)} items from {name} to {args.out}")
        return

    keys = [k["AttributeName"] for k in client.describe_table(TableName=name)["Table"]["KeySchema"]]
    if keys != ["pk", "sk"]:
        raise SystemExit(f"{name} has key {keys}, not pk/sk: create it first")
    items = json.loads(args.file.read_text(encoding="utf-8"))
    for item in items:
        item["pk"] = {"S": f"{args.prefix}#{item[args.key]['S']}"}
        item["sk"] = {"S": "META"}
        client.put_item(TableName=name, Item=item)
    print(f"restored {len(items)} items into {name}: {sorted(i['pk']['S'] for i in items)}")


if __name__ == "__main__":
    main()
