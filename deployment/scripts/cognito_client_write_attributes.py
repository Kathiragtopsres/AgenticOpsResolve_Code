"""Prepare a Cognito app-client update that makes the tenant (and technician) claims read-only for the user.

Read-only AWS calls only (describe-user-pool, describe-user-pool-client). It writes three files and prints
the mutating commands; it never runs them.

  <out>/<client>-before.json   the client exactly as it is now (the backup / rollback input)
  <out>/<client>-after.json    the same client with an explicit WriteAttributes list
  <out>/<client>-rollback.json the current client in the shape update-user-pool-client accepts (undo = apply it)

Why a full copy: ``update-user-pool-client`` resets every setting that is not passed, so the update must carry
the whole current configuration. ReadAttributes is left as it is (null = every attribute is readable, so
``custom:tenantId`` and ``custom:id`` stay in the ID token). WriteAttributes becomes every mutable attribute of
the pool except the ``--protect`` ones, so a user can still change their own profile but not their tenant.

Usage: python scripts/cognito_client_write_attributes.py --pool us-east-1_wJv8NbJQR \\
           --client 4hgmbfm02hnjv22bic3bdeu7gq --protect custom:tenantId --out <dir>
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

# Fields describe-user-pool-client returns that update-user-pool-client does not accept.
_READ_ONLY = {"CreationDate", "LastModifiedDate", "ClientSecret"}
# Cognito rejects these in an app client's WriteAttributes (InvalidParameterException).
_NOT_CLIENT_WRITABLE = ("identities", "email_verified", "phone_number_verified")


def _aws(*args: str) -> dict:
    out = subprocess.run(["aws", "cognito-idp", *args, "--region", "us-east-1", "--output", "json"],
                         check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pool", required=True)
    ap.add_argument("--client", required=True)
    ap.add_argument("--protect", nargs="+", required=True, help="attributes users must not be able to write")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    pool = _aws("describe-user-pool", "--user-pool-id", args.pool)["UserPool"]
    client = _aws("describe-user-pool-client", "--user-pool-id", args.pool, "--client-id", args.client)["UserPoolClient"]
    missing = [a for a in args.protect if a not in {s["Name"] for s in pool["SchemaAttributes"]}]
    if missing:
        raise SystemExit(f"not in the pool schema yet (add the custom attribute first): {missing}")

    writable = sorted(s["Name"] for s in pool["SchemaAttributes"]
                      if s.get("Mutable") and s["Name"] not in [*args.protect, *_NOT_CLIENT_WRITABLE])
    after = {k: v for k, v in client.items() if k not in _READ_ONLY} | {"WriteAttributes": writable}

    args.out.mkdir(parents=True, exist_ok=True)
    before_path, after_path = args.out / f"{args.client}-before.json", args.out / f"{args.client}-after.json"
    rollback_path = args.out / f"{args.client}-rollback.json"
    before_path.write_text(json.dumps(client, indent=2), encoding="utf-8")
    rollback_path.write_text(json.dumps({k: v for k, v in client.items() if k not in _READ_ONLY}, indent=2),
                             encoding="utf-8")
    after_path.write_text(json.dumps(after, indent=2), encoding="utf-8")
    print(f"WriteAttributes before: {client.get('WriteAttributes')}")
    print(f"WriteAttributes after : {writable}")
    print(f"backup: {before_path}\nrollback (mutates the app client): aws cognito-idp update-user-pool-client "
          f"--region us-east-1 --cli-input-json file://{rollback_path}\nApply (mutates the app client):\n"
          f"  aws cognito-idp update-user-pool-client --region us-east-1 --cli-input-json file://{after_path}")


if __name__ == "__main__":
    main()
