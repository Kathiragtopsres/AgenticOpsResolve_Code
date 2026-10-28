"""Case Context Envelope schema (Section 5.5, Listing 5-1).

The envelope is the only state passed to the agent per invocation, assembled from the
case store and trimmed to the token budget. Mirrors
schemas/case/case-context-envelope.schema.json.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from ...common.enums import CaseState, Channel, ConsentState


class TriggerRef(BaseModel):
    type: str
    name: str | None = None
    event_id: str | None = Field(default=None, alias="eventId")
    model_config = {"populate_by_name": True}


class ContactRef(BaseModel):
    contact_id: str = Field(alias="contactId")
    preferred_channel: Channel = Field(alias="preferredChannel")
    consent: dict[str, ConsentState] = Field(default_factory=dict)
    model_config = {"populate_by_name": True}


class CustomerRef(BaseModel):
    customer_id: str = Field(alias="customerId")
    name: str
    site: str | None = None
    contact: ContactRef | None = None
    model_config = {"populate_by_name": True}


class AssetRef(BaseModel):
    asset_id: str = Field(alias="assetId")
    model: str
    firmware: str | None = None
    model_config = {"populate_by_name": True}


class CaseContextEnvelope(BaseModel):
    case_id: str = Field(alias="caseId")
    correlation_id: str = Field(alias="correlationId")
    state: CaseState
    trigger: TriggerRef
    customer: CustomerRef
    asset: AssetRef | None = None
    entitlement: dict | None = None
    diagnosis: dict | None = None
    plan: dict | None = None
    appointment: dict | None = None
    open_waits: list[dict] = Field(default_factory=list, alias="openWaits")
    recent_timeline: list[str] = Field(default_factory=list, alias="recentTimeline")
    autonomy_profile: str = Field(default="DEFAULT_L2", alias="autonomyProfile")

    model_config = {"populate_by_name": True}
