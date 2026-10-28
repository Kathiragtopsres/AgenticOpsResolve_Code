#!/usr/bin/env python3
"""Seed (or reset to) the Ops Console demo baseline: 14 cases + 8 approvals.

Clears opsresolve-dev-cases / -approvals / -conversations, then writes the baseline from
opsresolve.console_api.scenario_seed. Unlike POST /scenario/reset it does NOT do the Reset
extras (free BOOKED availability windows, restore part stock, clear the Gateway demo tables,
demo work orders and cached tool results); it never touches mock-* or idempotency tables.

Usage (from tcs-agentic-opsresolve/):
  python scripts/seed_console_demo.py --dry-run      # print counts only, no AWS calls
  python scripts/seed_console_demo.py                # WRITES to AWS (asks for confirmation)
  python scripts/seed_console_demo.py --yes          # skip confirmation
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from opsresolve.console_api.scenario_seed import build_baseline, summarize  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="print item counts; do not write")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    a = p.parse_args()

    baseline = build_baseline()
    s = summarize(baseline)
    print(f"Baseline: {s['cases']} cases ({s['caseTableItems']} case-table items), "
          f"{s['approvals']} approvals ({s['pending']} PENDING)")
    if a.dry_run:
        print("Dry run: nothing written.")
        return 0

    from opsresolve.console_api import scenario_handler as h

    if not a.yes and input("This DELETES all items in cases/approvals/conversations and re-seeds. Type 'yes': ") != "yes":
        print("Aborted.")
        return 1
    log = h._Log()
    h._reset(log)
    for line in log.lines:
        print(f"[{line['level']}] {line['message']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
