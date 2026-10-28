"""Case state machine (Section 8.2).

Transitions are enforced by ``transitionCaseState`` using a DynamoDB conditional expression
on the current state and version, so illegal or concurrent transitions fail deterministically.
This module holds the pure transition table and guard used by that tool and by tests. It is
kept identical to the reference oracle (reference/opsresolve_ref/state_machine.py).
"""
from __future__ import annotations

TRANSITIONS: dict[str, set[str]] = {
    "OPEN": {"CONTEXT_READY"},
    "CONTEXT_READY": {"DIAGNOSING"},
    "DIAGNOSING": {"REMOTE_FIX_IN_PROGRESS", "AWAITING_CUSTOMER", "PLANNING_FIELD", "AWAITING_APPROVAL"},
    "AWAITING_CUSTOMER": {"DIAGNOSING", "PLANNING_FIELD"},
    "REMOTE_FIX_IN_PROGRESS": {"RESOLVED_REMOTE", "PLANNING_FIELD"},
    "RESOLVED_REMOTE": {"VERIFYING"},
    "PLANNING_FIELD": {"AWAITING_APPROVAL", "SCHEDULED"},
    "AWAITING_APPROVAL": {"PLANNING_FIELD", "DIAGNOSING", "REMOTE_FIX_IN_PROGRESS", "CANCELLED", "ESCALATED_HUMAN"},
    "SCHEDULED": {"AT_RISK", "IN_SERVICE"},
    "AT_RISK": {"SCHEDULED", "ESCALATED_HUMAN"},
    "IN_SERVICE": {"VERIFYING"},
    "VERIFYING": {"CLOSED", "PLANNING_FIELD"},
    "ESCALATED_HUMAN": {"PLANNING_FIELD", "CLOSED", "CANCELLED"},
    "CLOSED": set(),
    "CANCELLED": set(),
}
TERMINAL = {"CLOSED", "CANCELLED"}


class IllegalTransition(Exception):
    """Raised when a state transition is not permitted from the current state."""


def transition(current: str, target: str) -> str:
    """Return ``target`` if the transition is legal, else raise ``IllegalTransition``.

    A transition to ESCALATED_HUMAN is always permitted from any non-terminal state
    (the "Any -> ESCALATED_HUMAN" rule in Section 8.2).
    """
    if target == "ESCALATED_HUMAN" and current not in TERMINAL:
        return target
    if target not in TRANSITIONS.get(current, set()):
        raise IllegalTransition(f"{current} -> {target}")
    return target


def replay(states: list[str]) -> bool:
    """Validate a full sequence of states; raise on the first illegal step."""
    for a, b in zip(states, states[1:]):
        transition(a, b)
    return True
