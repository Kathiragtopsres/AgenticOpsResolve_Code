"""Communication Agent (Section 5.4.10).

Turns communication intents into channel-appropriate messages and maintains one
conversation across channels. The Channel Router (channels.router) selects the channel;
copy passes Bedrock Guardrails before send. L2 for informational and option messages.
"""
from __future__ import annotations

from .base import SpecialistSpec
from ..common.enums import AutonomyLevel

SPEC = SpecialistSpec(
    name="CommunicationAgent",
    purpose="Turn communication intents into channel-appropriate messages across channels.",
    tools=["sendCustomerMessage", "getContactPreferences"],
    autonomy=AutonomyLevel.L2,
    prompt_file="communication.v2.md",
)

RCS_TEXT_MAX_CHARS = 300
