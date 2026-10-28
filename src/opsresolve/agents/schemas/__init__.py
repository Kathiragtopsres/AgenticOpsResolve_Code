"""Pydantic output schemas for specialist agents (Section 5.1).

Every specialist returns JSON validated against one of these models; on validation
failure a single automatic repair retry is attempted, then the case escalates.
"""
from .case_context import CaseContextEnvelope
from .diagnosis import DiagnosisOutput

__all__ = ["CaseContextEnvelope", "DiagnosisOutput"]
