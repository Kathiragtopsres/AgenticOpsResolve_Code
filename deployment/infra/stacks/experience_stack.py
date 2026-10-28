"""Experience stack: customer channels + observability (merges former Channels, Observability stacks).

Ownership model (OPS-001):
  - Creates: SNS topics, SQS FIFO queue + DLQ, SES v2 identity/configuration set/templates,
    CloudWatch dashboards and alarms — all -owned (OPS-010/011/012, OPS-015/016).
  - RCS agent ID and WhatsApp Business Account (WABA) IDs are registered via the AWS
    console/CDS today (no stable CDK L2 yet); this stack only stores references to them
    (SSM parameters from FoundationStack) — it does not attempt to create the RCS/WA
    registrations themselves.
  - The Ops Console (OPS-014) is explicitly out of scope for this stack; see console_stack.py
    (left as an empty, unwired placeholder — owned by Subha).

Scope: dev environment, Scenario 1 (Cold-Chain Chiller, CX-500) only.
"""
from __future__ import annotations

from aws_cdk import Stack
from constructs import Construct

from .foundation_stack import FoundationStack


class ExperienceStack(Stack):
    """Customer-facing channels (AWS CDS) and platform observability.

    Resources:
      - SNS topics: opsresolve-<env>-inbound-rcs-sms, -inbound-wa, -msg-status
      - SQS FIFO opsresolve-<env>-inbound.fifo + DLQ (content-based dedup)
      - SES v2 verified identity, configuration set opsresolve-<env>-email + event destination
      - SES templates: opsr-booking-confirmation, opsr-service-report, opsr-escalation-notice
      - References (via SSM, provisioned in FoundationStack) to console-registered RCS agent
        ID and WABA IDs — not created here
      - CloudWatch dashboards: Business KPI, Agent quality, Channels, Platform
      - CloudWatch alarms: DLQ depth > 0; agent invocation error rate > 5% / 5 min;
        channel send failures > 3 / 5 min

    AgentCore Observability emits OTEL traces/metrics/logs to CloudWatch; Lambda functions
    use the ADOT layer so traces join across Runtime, Gateway, adapters and channel senders.
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

        # --- Channels (AWS CDS) ------------------------------------------------------------
        # TODO: sns.Topic inbound-rcs-sms / inbound-wa / msg-status
        # TODO: sqs.Queue inbound.fifo (fifo=True, content_based_deduplication=True) + DLQ
        # TODO: ses.CfnConfigurationSet + event destination to EventBridge bus
        # TODO: ses.CfnEmailTemplate x3 (opsr-booking-confirmation, opsr-service-report,
        #         opsr-escalation-notice)
        # NOTE: RCS agent ID / WABA IDs are registered out-of-band; read via SSM params
        #         provisioned in FoundationStack, not created here.

        self.inbound_topic = None
        self.inbound_queue = None
        self.ses_configuration_set = None

        # --- Observability -----------------------------------------------------------------
        # TODO: cloudwatch.Dashboard x4 (business_kpi, agent_quality, channels, platform)
        # TODO: cloudwatch.Alarm: DLQ depth > 0 (on self.inbound_queue's DLQ)
        # TODO: cloudwatch.Alarm: agent invocation error rate > 5% / 5 min
        # TODO: cloudwatch.Alarm: channel send failures > 3 / 5 min

        self.dashboards: dict[str, object] = {}
        self.alarms: dict[str, object] = {}
