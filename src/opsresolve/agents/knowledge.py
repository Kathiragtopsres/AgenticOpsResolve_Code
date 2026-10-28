"""Knowledge Agent (Section 5.4.4).

Retrieves validated manuals, SOPs and bulletins (Bedrock KB Retrieve with metadata
filters: assetModel, firmware, docStatus=ACTIVE) and produces a technician pre-job brief
(<= 250 words) with citations.
"""
from __future__ import annotations

from .base import SpecialistSpec
from ..common.enums import AutonomyLevel

SPEC = SpecialistSpec(
    name="KnowledgeAgent",
    purpose="Retrieve validated knowledge and produce a technician pre-job brief.",
    tools=["retrieveTroubleshootingKnowledge"],
    autonomy=AutonomyLevel.L0,
    prompt_file="knowledge.v3.md",
)

PRE_JOB_BRIEF_MAX_WORDS = 250
KB_TOP_K = 6
