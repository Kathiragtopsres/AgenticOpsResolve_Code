#!/usr/bin/env python3
"""Pre-deployment/readiness checks (Sections 15.2, 15.4).

Verifies Bedrock model access, CDS identities (RCS agent, WABA, SES), Knowledge Base sync,
and any one-time console steps that could not be captured as CDK constructs.

Usage:
    python scripts/preflight.py --env demo
"""
from __future__ import annotations

import argparse

CHECKS = [
    "Bedrock model access for the configured reasoning + lightweight models",
    "RCS agent registered and at least one test device accepted",
    "WABA linked to End User Messaging Social (if WhatsApp enabled)",
    "SES identity verified (domain or per-recipient)",
    "Knowledge Base ingestion job succeeded (docStatus filter returns ACTIVE only)",
    "SSM model parameters present under /opsresolve/<env>/models/*",
    "Postback HMAC key present in Secrets Manager",
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env", default="demo")
    args = ap.parse_args()
    print(f"Preflight for env '{args.env}':")
    for check in CHECKS:
        print(f"  [ ] {check}")
    print("TODO: implement live checks (boto3) and exit non-zero on any failure.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
