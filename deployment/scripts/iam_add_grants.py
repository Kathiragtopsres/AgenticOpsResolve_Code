"""Add the IAM grants the tenancy / Scenario 2 code needs to the LIVE inline policies (idempotent, additive).

Each statement has a Sid. A Sid that already exists in the live policy is left alone, so the script can be
re-run. Nothing is removed or changed. The live policy is backed up first.

  python scripts/iam_add_grants.py --backup-dir <dir>              # dry run: prints what would be added
  python scripts/iam_add_grants.py --backup-dir <dir> --apply      # backs up, then put-role-policy

Backup: <dir>/<role>.<policy>.before.json (put-role-policy input shape; rollback = put it back).
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

ACCT, REGION = "[AWS-ACCOUNT-NUMBER]", "us-east-1"


def _t(name: str) -> str:
    return f"arn:aws:dynamodb:{REGION}:{ACCT}:table/opsresolve-dev-{name}"


def _ddb(sid: str, actions: list[str], *tables: str) -> dict:
    return {"Sid": sid, "Effect": "Allow", "Action": [f"dynamodb:{a}" for a in actions],
            "Resource": [_t(t) for t in tables]}


# (role, inline policy name) -> statements to add
GRANTS: dict[tuple[str, str], list[dict]] = {
    ("opsresolve-dev-console-read-role", "opsresolve-dev-console-read"): [
        _ddb("TenantsRead", ["GetItem"], "tenants"),
    ],
    ("opsresolve-dev-console-approval-decision-role", "opsresolve-dev-console-approval-decision"): [
        _ddb("ReadCaseMetaForTenantCheck", ["GetItem"], "cases"),
        _ddb("PartRequestTechnicianRead", ["GetItem"], "mock-technicians"),
        _ddb("PartRequestStockUp", ["UpdateItem"], "mock-inventory"),
    ],
    ("opsresolve-dev-console-scenario-role", "opsresolve-dev-console-scenario"): [
        _ddb("TenantsReadWrite", ["GetItem", "PutItem"], "tenants"),
        _ddb("ReadDemoContact", ["GetItem"], "mock-contacts"),
        _ddb("SeedS2TechnicianWindows", ["PutItem"], "field-availability"),
        {"Sid": "ScanCustomerText", "Effect": "Allow", "Action": ["bedrock:ApplyGuardrail"],
         "Resource": [f"arn:aws:bedrock:{REGION}:{ACCT}:guardrail/xa0zjafyaz7z"]},
    ],
    ("opsresolve-dev-inbound-normalizer-role", "opsresolve-dev-inbound-normalizer"): [
        _ddb("TenantsRead", ["GetItem"], "tenants"),
    ],
    ("OpsResolve-target-tools-lambda-role-542zgxm6", "opsresolve-dev-gateway-tools"): [
        _ddb("S2MockRead", ["GetItem", "Scan"], "mock-assets", "mock-contracts", "mock-couriers", "mock-depots"),
        _ddb("S2NetworkProfile", ["GetItem", "UpdateItem"], "mock-network"),
    ],
}


def _aws(*args: str) -> dict:
    out = subprocess.run(["aws", "iam", *args, "--output", "json"], check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backup-dir", required=True, type=Path)
    ap.add_argument("--apply", action="store_true", help="really put the policies (default is a dry run)")
    args = ap.parse_args()
    args.backup_dir.mkdir(parents=True, exist_ok=True)

    for (role, policy), statements in GRANTS.items():
        live = _aws("get-role-policy", "--role-name", role, "--policy-name", policy)["PolicyDocument"]
        have = {s.get("Sid") for s in live["Statement"]}
        todo = [s for s in statements if s["Sid"] not in have]
        print(f"{role} / {policy}: {len(live['Statement'])} statements, adding {[s['Sid'] for s in todo] or 'nothing'}")
        if not todo or not args.apply:
            continue
        backup = args.backup_dir / f"{role}.{policy}.before.json"
        backup.write_text(json.dumps(live, indent=2), encoding="utf-8")
        new = live | {"Statement": live["Statement"] + todo}
        doc = args.backup_dir / f"{role}.{policy}.after.json"
        doc.write_text(json.dumps(new, indent=2), encoding="utf-8")
        subprocess.run(["aws", "iam", "put-role-policy", "--role-name", role, "--policy-name", policy,
                        "--policy-document", f"file://{doc.resolve().as_posix()}"], check=True)
        print(f"  applied; rollback: aws iam put-role-policy --role-name {role} --policy-name {policy} "
              f"--policy-document file://{backup.resolve().as_posix()}")
    if not args.apply:
        print("dry run only; add --apply to write")


if __name__ == "__main__":
    main()
