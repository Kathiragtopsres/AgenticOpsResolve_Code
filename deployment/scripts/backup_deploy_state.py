"""Back up everything the S2 deployment touches (read-only AWS calls) into one folder.

  python scripts/backup_deploy_state.py --out .agents/backups/<ts>-s2

Per Lambda: configuration JSON + the deployed code zip. Per role: inline policies. The Gateway target. The two Cognito
app clients and pools. Items of the mock-* tables the deployment writes or re-creates (typed DynamoDB JSON), the
field-availability table, and item counts of the case-store tables (cases / approvals / conversations are wiped by
Reset, so dump them too only if you need them: add --case-store).

The folder contains phone numbers (mock-contacts, mock-technicians): keep it out of git and never paste it anywhere.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import urllib.request
from pathlib import Path

import boto3

REGION = "us-east-1"
LAMBDAS = [
    "opsresolve-dev-agent-invoker", "OpsResolve-target-tools-lambda", "opsresolve-dev-inbound-normalizer",
    "opsresolve-dev-console-read", "opsresolve-dev-console-approval-decision", "opsresolve-dev-console-scenario",
    "opsresolve-dev-field-read", "opsresolve-dev-field-actions", "opsresolve-dev-field-uploads",
    "opsresolve-dev-field-assistant", "opsresolve-dev-availability-slots",
]
ROLES = {
    "opsresolve-dev-console-read-role": "opsresolve-dev-console-read",
    "opsresolve-dev-console-approval-decision-role": "opsresolve-dev-console-approval-decision",
    "opsresolve-dev-console-scenario-role": "opsresolve-dev-console-scenario",
    "opsresolve-dev-inbound-normalizer-role": "opsresolve-dev-inbound-normalizer",
    "OpsResolve-target-tools-lambda-role-542zgxm6": "opsresolve-dev-gateway-tools",
}
TABLES = ["mock-parts", "mock-work-order-history", "mock-inventory", "mock-network", "mock-depots", "mock-couriers",
          "mock-assets", "mock-contracts", "mock-contacts", "mock-technicians", "mock-customers", "mock-sites",
          "mock-approvers", "field-availability", "reservations", "transfers", "appointments", "remote-actions"]
CASE_STORE = ["cases", "approvals", "conversations", "idempotency"]
POOLS = {"us-east-1_wJv8NbJQR": "4hgmbfm02hnjv22bic3bdeu7gq", "us-east-1_hZ90xjeek": "2dqi4dqd31bl2hhjg2k457rg7i"}


def _save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _scan(client, table: str) -> list[dict]:
    items, kw = [], {"TableName": table}
    while True:
        resp = client.scan(**kw)
        items += resp["Items"]
        if "LastEvaluatedKey" not in resp:
            return items
        kw["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--case-store", action="store_true", help="also dump cases / approvals / conversations / idempotency")
    args = ap.parse_args()
    out = args.out
    lam, iam = boto3.client("lambda", region_name=REGION), boto3.client("iam")
    ddb, cog = boto3.client("dynamodb", region_name=REGION), boto3.client("cognito-idp", region_name=REGION)

    for fn in LAMBDAS:
        try:
            got = lam.get_function(FunctionName=fn)
        except lam.exceptions.ResourceNotFoundException:
            print(f"skip {fn}: not found")
            continue
        _save(out / "lambda" / f"{fn}.config.json", got["Configuration"])
        (out / "lambda" / f"{fn}.zip").write_bytes(urllib.request.urlopen(got["Code"]["Location"]).read())
        print(f"lambda {fn}: config + {got['Configuration']['CodeSize']} byte zip")
    for role, policy in ROLES.items():
        _save(out / "iam" / f"{role}.{policy}.json", iam.get_role_policy(RoleName=role, PolicyName=policy)["PolicyDocument"])
    print(f"iam: {len(ROLES)} inline policies")
    gw = subprocess.run(["aws", "bedrock-agentcore-control", "get-gateway-target", "--region", REGION,
                         "--gateway-identifier", "opsresolve-gateway-4pqnd9tdmj", "--target-id", "MHLA1ND4PO",
                         "--output", "json"], check=True, capture_output=True, text=True, encoding="utf-8",
                        errors="replace").stdout
    (out / "gateway-target.json").write_text(gw, encoding="utf-8")
    for pool, client_id in POOLS.items():
        _save(out / "cognito" / f"{pool}.pool.json", cog.describe_user_pool(UserPoolId=pool)["UserPool"])
        _save(out / "cognito" / f"{client_id}.client.json",
              cog.describe_user_pool_client(UserPoolId=pool, ClientId=client_id)["UserPoolClient"])
    print("gateway target + cognito pools / clients")
    for t in TABLES + (CASE_STORE if args.case_store else []):
        name = f"opsresolve-dev-{t}"
        try:
            items = _scan(ddb, name)
        except ddb.exceptions.ResourceNotFoundException:
            print(f"skip {name}: not found")
            continue
        _save(out / "tables" / f"{t}.json", items)
        print(f"table {t}: {len(items)} items")
    for t in ([] if args.case_store else CASE_STORE):
        print(f"count {t}: {ddb.describe_table(TableName=f'opsresolve-dev-{t}')['Table']['ItemCount']} (not dumped)")
    print(f"backup written to {out}")


if __name__ == "__main__":
    main()
