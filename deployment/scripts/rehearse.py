#!/usr/bin/env python3
"""Demo rehearsal harness (Section 16.3).

Runs the full storyboard from a clean state: reset -> execute the scenario via the Scenario
Controller -> record timings and failures. The submission gate is 19/20 consecutive
successful runs (NFR-05) before recording the video.

Usage:
    python scripts/rehearse.py --env demo --scenario s1-cold-chain-chiller
"""
from __future__ import annotations

import argparse


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env", default="demo")
    ap.add_argument("--scenario", default="s1-cold-chain-chiller")
    ap.add_argument("--runs", type=int, default=1)
    args = ap.parse_args()
    print(f"Rehearsing {args.scenario} on env '{args.env}' x{args.runs}")
    print("TODO: drive reset_demo -> telemetry_sim -> assert golden timeline; log timings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
