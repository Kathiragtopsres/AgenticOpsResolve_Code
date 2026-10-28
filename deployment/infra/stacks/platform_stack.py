"""Platform stack: event fabric + tools/mocks + AgentCore (merges former Events, Tools, AgentCore stacks).

Ownership model (OPS-001):
  - Creates: EventBridge bus/rules/archive, Scheduler group, IoT topic rule, Lambda tool
    adapters (src/opsresolve/tools/*), mock enterprise API Lambdas (src/opsresolve/mocks/*),
    AgentCore Gateway/Policy/Identity/Memory, the Agent Invoker Lambda, and the AgentCore
    Runtime registration for opsresolve-agent.
  - Anomaly detection (OPS-004, Subha's ML model) is explicitly OUT of scope. The IoT topic
    rule stops at an integration seam: it lands telemetry on the bus / a placeholder target
    so the ML team can attach their detector independently. We do not build or own that model.
  - Lambda functions and the AgentCore Runtime reuse the 2 existing shared IAM execution
    roles from FoundationStack (opsreslove-lambda-exe-role, opsresolve-agentcore-agent-exe-role).
    No new IAM roles are authored for these; CDK auto-created roles for unrelated constructs
    (e.g. custom resource providers) are acceptable.
  - Reads/writes against Subha's case-store tables go through FoundationStack's imported
    table references — this stack never creates those tables.

Scope: dev environment, Scenario 1 (Cold-Chain Chiller, CX-500) only.
"""
from __future__ import annotations

from aws_cdk import Stack
from constructs import Construct

from .foundation_stack import FoundationStack

# IoT Core topic rule for S1 chiller telemetry (Section 9.4, Listing 9-1 of the Technical
# Specification). Debounce (3 breaches in 5 min -> one event per asset per 30 min) is
# implemented in the anomaly Lambda (OPS-004, out of scope here), not in this SQL filter.
S1_TELEMETRY_TOPIC_RULE_SQL = (
    "SELECT *, topic(3) AS assetId FROM 'opsresolve/telemetry/+' "
    "WHERE compressorPressureVar > 0.18 OR supplyTempC > 9.0"
)

# Cedar policy bundle (single file covering all domains for the current scope).
CEDAR_POLICY_PATH = "../policies/opsresolve.cedar"

# Tool adapter modules wired as one Lambda per domain (src/opsresolve/tools/*.py).
TOOL_DOMAINS = ["context", "knowledge", "parts", "workforce", "scheduling", "comms", "case"]

# Mock enterprise system modules (src/opsresolve/mocks/*.py) — S1 needs CRM, EAM, ERP, FSM.
# device.py (device management) backs S2/S3 and is not wired for the S1-only scope.
MOCK_SYSTEMS_S1 = ["crm", "eam", "erp", "fsm"]


class PlatformStack(Stack):
    """Event fabric, governed tools, mocks, and the AgentCore agentic core.

    Resources:
      - EventBridge custom bus opsresolve-<env>-bus + 7-day archive (replay)
      - Rules routing detail-types to the Agent Invoker Lambda
      - EventBridge Scheduler group for SLA/ETA/approval/verify timers
      - IoT Core topic rule (opsresolve/telemetry/+) for S1 chiller telemetry, landing on
        the bus; anomaly detection (OPS-004) attaches downstream and is out of scope here
      - Lambda tool adapters: context, knowledge, parts, workforce, scheduling, comms, case
      - Mock enterprise APIs (API Gateway + Lambda) for CRM, EAM, ERP, FSM (S1 scope)
      - AgentCore Runtime registration for "opsresolve-agent" (Strands, Python 3.12),
        using the existing opsresolve-agentcore-agent-exe-role
      - AgentCore Gateway "opsresolve-gateway" (MCP) + targets grouped by tool domain
      - AgentCore Policy engine loaded with Cedar policies from ../policies/opsresolve.cedar
      - AgentCore Identity (workload identity) + Memory (session=caseId, actor=contactId)
      - Agent Invoker Lambda mapping EventBridge events to InvokeAgentRuntime (idempotent
        on eventId, using FoundationStack's imported idempotency table)

    Where a CDK L2 construct is not yet available (AgentCore Policy, Runtime registration,
    Gateway targets), use a custom resource calling the service API, or document a one-time
    console step captured in deploy.md and verified by scripts/preflight.py.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        foundation: FoundationStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.env_name = env_name
        self.prefix = f"opsresolve-{env_name}"
        self.foundation = foundation

        # --- Event fabric ----------------------------------------------------------------
        # TODO: events.EventBus(self, "OpsResolveBus", event_bus_name=f"{self.prefix}-bus")
        # TODO: events.Archive(...)  # 7-day retention, replay support
        # TODO: events.Rule x N routing detail-types -> Agent Invoker Lambda target
        # TODO: scheduler.CfnScheduleGroup(self, "TimersGroup", name=f"{self.prefix}-timers")
        # TODO: iot.CfnTopicRule(self, "S1TelemetryRule", topic_rule_payload=dict(
        #         sql=S1_TELEMETRY_TOPIC_RULE_SQL, actions=[<put to bus / firehose>]))
        #         # Integration seam only: no anomaly-detection logic lives here (OPS-004).

        self.bus = None
        self.archive = None
        self.scheduler_group = None
        self.telemetry_rule = None

        # --- Lambda tool adapters + mocks (reuse shared lambda exec role) ---------------
        # TODO: for domain in TOOL_DOMAINS: lambda_.Function(self, f"Tool{domain.title()}",
        #         code from src/opsresolve/tools/{domain}.py,
        #         role=foundation.lambda_exec_role, environment={...table/bucket names...})
        # TODO: for system in MOCK_SYSTEMS_S1: lambda_.Function + apigateway.RestApi mock
        #         endpoint backed by src/opsresolve/mocks/{system}.py, role=foundation.lambda_exec_role
        # TODO: idempotency wiring via foundation.idempotency_table (24h TTL, key + response hash)

        self.tool_functions: dict[str, object] = {}
        self.mock_apis: dict[str, object] = {}

        # --- AgentCore ---------------------------------------------------------------------
        # TODO: agent runtime container asset (src/opsresolve/agents) + AgentCore Runtime
        #         custom resource, execution_role_arn=foundation.agentcore_exec_role.role_arn
        # TODO: AgentCore Gateway "opsresolve-gateway" (MCP) + targets per TOOL_DOMAINS
        # TODO: AgentCore Identity (workload identity)
        # TODO: AgentCore Memory (session=caseId, actor=contactId)
        # TODO: AgentCore Policy engine + Cedar policy deployment from CEDAR_POLICY_PATH
        # TODO: Agent Invoker Lambda (role=foundation.lambda_exec_role) subscribed to bus rules,
        #         idempotent on eventId via foundation.idempotency_table

        self.agent_runtime = None
        self.gateway = None
        self.policy_engine = None
        self.identity = None
        self.memory = None
        self.agent_invoker = None
