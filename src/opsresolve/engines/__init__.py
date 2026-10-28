"""Deterministic engines (Sections 5.4.7, 8.2, 8.5).

These are pure, testable, LLM-free components:
  state_machine        Case state transition table and guard (Section 8.2)
  technician_scoring   Eligibility + soft-score ranking (Section 5.4.7)
  recovery_risk        Risk scoring + recovery option scoring (Section 8.5)
  slot_engine          Feasible slot proposal (Section 5.4.8 / 8.5)

The reference oracles in ``reference/opsresolve_ref/`` are the authoritative specification;
contract tests assert these production modules produce identical results.
"""
