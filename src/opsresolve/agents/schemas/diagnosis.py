"""Diagnostic Agent output schema (Section 5.4.2).

Mirrors schemas/case/diagnosis-output.schema.json. Every probable cause must cite at
least one evidence reference (KB chunk id or telemetry signal).
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class RecommendedPath(str, Enum):
    REMOTE = "REMOTE"
    FIELD = "FIELD"
    ESCALATE = "ESCALATE"


class ProbableCause(BaseModel):
    cause: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list, alias="evidenceRefs", min_length=1)


class DiagnosisOutput(BaseModel):
    probable_causes: list[ProbableCause] = Field(alias="probableCauses")
    remote_action_candidate: str | None = Field(default=None, alias="remoteActionCandidate")
    safety_flag: bool = Field(default=False, alias="safetyFlag")
    clarifying_question: str | None = Field(default=None, alias="clarifyingQuestion")
    recommended_path: RecommendedPath = Field(alias="recommendedPath")

    model_config = {"populate_by_name": True}
