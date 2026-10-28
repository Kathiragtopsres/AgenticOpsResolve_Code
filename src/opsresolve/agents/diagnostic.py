"""Diagnostic Agent (Section 5.4.2).

Fault isolation from telemetry, history and validated knowledge; decides whether a safe
remote action exists. Every cause must cite at least one KB chunk or telemetry signal;
confidence < 0.55 or safetyFlag=true forces ESCALATE.
"""
from __future__ import annotations

from .base import SpecialistSpec
from ..common.enums import AutonomyLevel

SPEC = SpecialistSpec(
    name="DiagnosticAgent",
    purpose="Isolate faults and decide REMOTE | FIELD | ASK_CUSTOMER | ESCALATE with cited evidence.",
    tools=[
        "getAssetHealth",
        "getServiceHistory",
        "retrieveTroubleshootingKnowledge",
        "executeSafeRemoteAction",
    ],
    autonomy=AutonomyLevel.L2,  # L2 on allow-list, L4 otherwise
    prompt_file="diagnostic.v7.md",
)

CONFIDENCE_ESCALATE_THRESHOLD = 0.55
MAX_CLARIFYING_QUESTIONS = 2
