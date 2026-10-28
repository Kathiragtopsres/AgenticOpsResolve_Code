"""OpsResolve agent service (Section 5).

A Supervisor (Orchestrator) coordinates 10 specialist agents exposed as tools
("agents-as-tools"), packaged as one AgentCore Runtime agent (``opsresolve-agent``)
built on the Strands Agents SDK.

Modules:
  orchestrator          Service Resolution Orchestrator (5.4.1)
  diagnostic            Diagnostic Agent (5.4.2)
  asset_context         Asset Context Agent (5.4.3, deterministic)
  knowledge             Knowledge Agent (5.4.4)
  entitlement           SLA and Entitlement Agent (5.4.5)
  parts                 Parts Agent (5.4.6)
  technician_matching   Technician Matching Agent (5.4.7)
  scheduling            Scheduling and Dispatch Agent (5.4.8)
  service_recovery      Service Recovery Agent (5.4.9)
  communication         Communication Agent (5.4.10)
  completion            Completion Agent (5.4.11)
"""
