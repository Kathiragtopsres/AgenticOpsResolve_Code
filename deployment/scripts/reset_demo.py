#!/usr/bin/env python3
"""Reset the demo environment to a clean state in under 60 seconds (Section 15.5).

Clears cases, conversations, timers, idempotency records and mock transactional data,
restores seed state, resets the telemetry simulator, and verifies readiness. Also exposed
as a button in the Ops Console Scenario Controller.

Usage:
    python scripts/reset_demo.py --env demo [--dry-run]
"""
from __future__ import annotations

import argparse

STEPS = [
    "Purge opsresolve-<env>-cases (cases, timeline, plans, approvals, messages)",
    "Purge opsresolve-<env>-conversations and opsresolve-<env>-idempotency",
    "Reset mock transactional tables (reservations, work orders) to seed state",
    "Delete pending EventBridge Scheduler timers (sched-<caseId>-*)",
    "Reset telemetry simulator to the 'normal' profile",
    "Verify readiness (tables reachable, seed present)",
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env", default="demo")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    print(f"Reset demo for env '{args.env}'" + (" (dry-run)" if args.dry_run else ""))
    for step in STEPS:
        print(f"  - {step}")
    print("TODO: implement the destructive reset with boto3 (guard behind --env allow-list).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
