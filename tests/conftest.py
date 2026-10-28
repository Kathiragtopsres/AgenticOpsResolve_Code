"""Pytest configuration for the OpsResolve test suite.

Ensures the application package (``src/opsresolve``) and the reference oracles
(``reference/opsresolve_ref``) are importable regardless of how pytest is invoked.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT / "reference"):
    p = str(path)
    if p not in sys.path:
        sys.path.insert(0, p)
