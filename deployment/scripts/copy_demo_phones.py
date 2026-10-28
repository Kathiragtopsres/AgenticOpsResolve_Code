"""One-off: point the S2 demo contact and technician at the real demo phones (decision D6). Prints no phone number.

  CONTACTS#CON-77      (S1, Anita Rao)  phone / whatsappId  ->  CONTACTS#CON-2201  (S2, Priya Nair)
  TECHNICIANS#TECH-204 (S1, R. Kumar)   phone               ->  TECHNICIANS#TEL-801 (S2, A. Iyer)

Only the phone attributes move; email and everything else on the target item stay. Both target items are backed up
to --backup-dir first (they hold the phone numbers, keep that folder out of git). Run it AFTER the S2 seed load, which
overwrites these two items. Re-running is harmless.

  python scripts/copy_demo_phones.py --backup-dir <dir> [--dry-run]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import boto3

PAIRS = [  # (table, source pk, target pk, attributes to copy)
    ("opsresolve-dev-mock-contacts", "CONTACTS#CON-77", "CONTACTS#CON-2201", ("phone", "whatsappId")),
    ("opsresolve-dev-mock-technicians", "TECHNICIANS#TECH-204", "TECHNICIANS#TEL-801", ("phone",)),
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backup-dir", required=True, type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    args.backup_dir.mkdir(parents=True, exist_ok=True)
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    for table, src_pk, dst_pk, attrs in PAIRS:
        t = ddb.Table(table)
        src = t.get_item(Key={"pk": src_pk, "sk": "META"}).get("Item")
        dst = t.get_item(Key={"pk": dst_pk, "sk": "META"}).get("Item")
        if not src or not dst:
            raise SystemExit(f"{table}: {'source' if not src else 'target'} item is missing "
                             f"({src_pk} -> {dst_pk}); load the S2 seed first")
        present = [a for a in attrs if src.get(a)]
        missing = [a for a in attrs if a not in present]
        print(f"{table}: {src_pk} -> {dst_pk}: copy {present}" + (f", source has no {missing}" if missing else ""))
        if args.dry_run or not present:
            continue
        (args.backup_dir / f"{table}.{dst_pk.replace('#', '_')}.before.json").write_text(
            json.dumps(dst, indent=2, default=str), encoding="utf-8")
        t.update_item(
            Key={"pk": dst_pk, "sk": "META"},
            UpdateExpression="SET " + ", ".join(f"#a{i} = :v{i}" for i in range(len(present))),
            ExpressionAttributeNames={f"#a{i}": a for i, a in enumerate(present)},
            ExpressionAttributeValues={f":v{i}": src[a] for i, a in enumerate(present)},
            ConditionExpression="attribute_exists(pk)")
        print("  copied")


if __name__ == "__main__":
    main()
