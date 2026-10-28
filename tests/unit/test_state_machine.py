"""Unit tests for common/state_machine.py transition guards (OPS-005+006).

Expands beyond the 4 cases already covered in test_router_state.py (UT-SM-01..04,
against the reference oracle opsresolve_ref.state_machine): this file exercises the
production module directly (src/opsresolve/common/state_machine.py) and covers every
legal edge in TRANSITIONS, illegal-skip rejection, the "ESCALATED_HUMAN from any
non-terminal state" rule, and terminal states rejecting all transitions.
"""
from __future__ import annotations

import pytest

from opsresolve.common.state_machine import TERMINAL, TRANSITIONS, IllegalTransition, replay, transition

# Flattened (from, to) pairs for every legal edge in TRANSITIONS.
LEGAL_EDGES = [(src, dst) for src, targets in TRANSITIONS.items() for dst in targets]

# Every state that appears anywhere in TRANSITIONS (as a source or a target).
ALL_STATES_SEEN = set(TRANSITIONS) | {dst for targets in TRANSITIONS.values() for dst in targets}


def test_all_states_have_a_transitions_entry():
    """Every state referenced anywhere in the table has its own (possibly empty) entry --
    guards against a typo'd target state with no corresponding source-state row."""
    assert ALL_STATES_SEEN == set(TRANSITIONS)


@pytest.mark.parametrize("src,dst", LEGAL_EDGES)
def test_every_legal_edge_is_accepted(src, dst):
    assert transition(src, dst) == dst


def test_legal_edges_cover_full_s1_golden_path():
    """Cross-check: the golden-path sequence used in test_router_state.py's UT-SM-01
    stays legal against the production table too (not just the reference oracle)."""
    assert replay(
        [
            "OPEN", "CONTEXT_READY", "DIAGNOSING", "REMOTE_FIX_IN_PROGRESS",
            "PLANNING_FIELD", "SCHEDULED", "AT_RISK", "SCHEDULED", "IN_SERVICE",
            "VERIFYING", "CLOSED",
        ]
    )


# Illegal-skip cases: pick pairs that are NOT in LEGAL_EDGES and are not the universal
# ESCALATED_HUMAN allowance, i.e. genuine skips/backwards jumps that must be rejected.
ILLEGAL_SKIPS = [
    ("OPEN", "DIAGNOSING"),               # skips CONTEXT_READY
    ("OPEN", "SCHEDULED"),                # skips everything
    ("CONTEXT_READY", "PLANNING_FIELD"),  # skips DIAGNOSING
    ("DIAGNOSING", "SCHEDULED"),          # skips PLANNING_FIELD (UT-SM-02 case)
    ("DIAGNOSING", "VERIFYING"),          # skips remote-fix/field entirely
    ("SCHEDULED", "VERIFYING"),           # skips IN_SERVICE
    ("SCHEDULED", "CLOSED"),
    ("PLANNING_FIELD", "IN_SERVICE"),     # skips SCHEDULED
    ("AT_RISK", "IN_SERVICE"),            # AT_RISK only legally goes to SCHEDULED/ESCALATED_HUMAN
    ("VERIFYING", "SCHEDULED"),           # backwards jump not on the table
    ("RESOLVED_REMOTE", "CLOSED"),        # must pass through VERIFYING
]


@pytest.mark.parametrize("src,dst", ILLEGAL_SKIPS)
def test_illegal_skip_rejected(src, dst):
    with pytest.raises(IllegalTransition):
        transition(src, dst)


NON_TERMINAL_STATES = sorted(set(TRANSITIONS) - TERMINAL)


@pytest.mark.parametrize("src", NON_TERMINAL_STATES)
def test_escalated_human_reachable_from_every_non_terminal_state(src):
    assert transition(src, "ESCALATED_HUMAN") == "ESCALATED_HUMAN"


@pytest.mark.parametrize("terminal", sorted(TERMINAL))
def test_escalated_human_not_reachable_from_terminal_states(terminal):
    """The "Any -> ESCALATED_HUMAN" rule explicitly excludes terminal states -- a closed
    or cancelled case cannot be escalated (it can only stay put, since TRANSITIONS[terminal]
    is empty)."""
    with pytest.raises(IllegalTransition):
        transition(terminal, "ESCALATED_HUMAN")


@pytest.mark.parametrize(
    "terminal,target",
    [
        (terminal, target)
        for terminal in sorted(TERMINAL)
        for target in sorted(set(TRANSITIONS) - {terminal})
    ],
)
def test_terminal_states_reject_every_outbound_transition(terminal, target):
    assert TRANSITIONS[terminal] == set()
    with pytest.raises(IllegalTransition):
        transition(terminal, target)


def test_replay_raises_on_first_illegal_step():
    with pytest.raises(IllegalTransition, match="DIAGNOSING -> SCHEDULED"):
        replay(["OPEN", "CONTEXT_READY", "DIAGNOSING", "SCHEDULED"])
