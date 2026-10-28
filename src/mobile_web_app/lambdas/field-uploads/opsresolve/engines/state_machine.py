"""Case state machine (Section 8.2).

Re-exports from common.state_machine for backward compatibility.
Transitions are enforced by ``transitionCaseState`` using a DynamoDB conditional expression
on the current state and version, so illegal or concurrent transitions fail deterministically.
This module holds the pure transition table and guard used by that tool and by tests. It is
kept identical to the reference oracle (reference/opsresolve_ref/state_machine.py).
"""
from __future__ import annotations

# Re-export from common for backward compatibility
from opsresolve.common.state_machine import (
    TERMINAL,
    TRANSITIONS,
    IllegalTransition,
    replay,
    transition,
)

__all__ = ["TRANSITIONS", "TERMINAL", "IllegalTransition", "transition", "replay"]
