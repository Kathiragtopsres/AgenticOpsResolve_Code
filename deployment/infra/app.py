#!/usr/bin/env python3
"""AWS CDK application entry point for TCS Agentic OpsResolve.

Synthesizes all stacks for a given environment (dev | demo). The environment is
selected via CDK context, e.g.:

    cdk deploy --all -c env=demo

Stacks are wired in dependency order (OPS-001 consolidation: Foundation -> Platform ->
Experience). Scope is currently Scenario 1 (Cold-Chain Chiller) only, dev environment
first. The Ops Console (OPS-014, Subha) is intentionally not wired here yet — see
stacks/console_stack.py, which is left as an empty, unwired placeholder.
"""
from __future__ import annotations

import aws_cdk as cdk

from stacks.experience_stack import ExperienceStack
from stacks.foundation_stack import FoundationStack
from stacks.platform_stack import PlatformStack

app = cdk.App()

env_name = app.node.try_get_context("env") or "dev"
region = app.node.try_get_context("opsresolve:defaultRegion") or "us-east-1"
aws_env = cdk.Environment(region=region)

prefix = f"opsresolve-{env_name}"


def stack_id(name: str) -> str:
    return f"OpsResolve-{env_name}-{name}"


# 1. Foundation: -owned S3 bucket, Secrets, SSM params + imports of team-owned
#    resources (3 ML/KB S3 buckets, Bedrock KB + Guardrail, 2 shared IAM exec roles,
#    Subha's case-store DynamoDB tables).
foundation = FoundationStack(app, stack_id("Foundation"), env_name=env_name, env=aws_env)

# 2. Platform: EventBridge bus/rules/archive/scheduler, IoT telemetry rule (anomaly
#    detection integration seam only — OPS-004 is owned elsewhere), Lambda tool adapters,
#    mock enterprise services, and AgentCore (Runtime/Gateway/Policy/Identity/Memory/Invoker).
platform = PlatformStack(app, stack_id("Platform"), env_name=env_name, foundation=foundation, env=aws_env)

# 3. Experience: Channels (SNS/SQS FIFO, SES, RCS/WhatsApp references) + Observability
#    (dashboards/alarms).
experience = ExperienceStack(app, stack_id("Experience"), env_name=env_name, foundation=foundation, env=aws_env)

cdk.Tags.of(app).add("project", "opsresolve")
cdk.Tags.of(app).add("environment", env_name)

app.synth()
