"""Contract tests: production engines match the reference oracles (Sections 5.4.7, 8.2, 8.5)."""
from __future__ import annotations

from datetime import datetime

from opsresolve.engines import recovery_risk as prod_risk
from opsresolve.engines import state_machine as prod_sm
from opsresolve_ref import engines as ref_eng
from opsresolve_ref import state_machine as ref_sm


def test_state_transitions_match() -> None:
    assert prod_sm.TRANSITIONS == ref_sm.T
    assert prod_sm.TERMINAL == ref_sm.TERMINAL


def test_recovery_risk_matches() -> None:
    now = datetime(2026, 10, 20, 12, 0, 0)
    slot = datetime(2026, 10, 20, 14, 0, 0)
    restore_by = datetime(2026, 10, 20, 16, 30, 0)
    prod = prod_risk.risk(now, 45, 20, slot, restore_by, 60)
    ref = ref_eng.risk(now, 45, 20, slot, restore_by, 60)
    assert prod == ref
