#!/usr/bin/env python3
"""Seed all scenarios' mock data for an environment (Section 15.4).

Wraps ``load_seed.py`` to load every scenario found under ``seed/`` into the
``opsresolve-<env>-mock-*`` DynamoDB tables.

Usage:
    python scripts/seed.py --env demo [--dry-run]
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCENARIOS = sorted(p.name for p in (ROOT / "seed").iterdir() if p.is_dir())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env", default="demo")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    load_seed = ROOT / "scripts" / "load_seed.py"
    for scenario in SCENARIOS:
        cmd = [sys.executable, str(load_seed), "--scenario", scenario, "--env", args.env]
        if args.dry_run:
            cmd.append("--dry-run")
        print(f"==> seeding {scenario}")
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            return result.returncode
    print(f"Seeded {len(SCENARIOS)} scenario(s) into env '{args.env}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
