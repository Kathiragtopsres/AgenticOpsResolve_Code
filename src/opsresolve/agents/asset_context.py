"""Asset Context Agent (Section 5.4.3).

Deterministic (no LLM): assembles customer, site, asset, contract and service-history
context via parallel tool calls, merged into the Case Context Envelope.
"""
from __future__ import annotations

from .base import SpecialistSpec
from ..common.enums import AutonomyLevel

SPEC = SpecialistSpec(
    name="AssetContextAgent",
    purpose="Assemble customer, site, asset, contract and service-history context.",
    tools=["getAssetContext", "getServiceHistory"],
    autonomy=AutonomyLevel.L0,
    prompt_file="asset_context.v2.md",
    deterministic=True,
)
