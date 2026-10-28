"""TCS Agentic OpsResolve — application source package.

Subpackages:
  agents    Supervisor orchestrator + 10 specialist agents (Strands, agents-as-tools)
  tools     Lambda tool adapters grouped by domain + JSON Schemas
  channels  RCS, SMS, WhatsApp, SES senders + the Channel Router
  inbound   Inbound normalizer and postback signer/verifier
  engines   Deterministic engines: slot, technician scoring, recovery risk
  mocks     Mock enterprise systems (CRM, EAM, ERP, WFM/FSM, device management)
  common    Shared models, enums, events, config and logging helpers
"""

__version__ = "1.0.0"
