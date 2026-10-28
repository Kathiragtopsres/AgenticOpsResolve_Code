"""Console stack: CloudFront + S3 + Cognito + REST/WebSocket APIs for the Ops Console.

NOT IN SCOPE for 's current tasks (OPS-001 consolidation). The Ops Console is
OPS-014, owned by Subha. This file is intentionally left as an empty, unwired placeholder
(not imported by app.py) — left in place at the user's request rather than deleted.
Its former dependency on data_stack.DataStack was removed when that stack was merged
away during the 8-to-3 stack consolidation; restore/rework this stack when OPS-014 starts.
"""
from __future__ import annotations

from aws_cdk import Stack
from constructs import Construct

from .foundation_stack import FoundationStack


class ConsoleStack(Stack):
    """Ops Console hosting and APIs (Sections 14, 15.2).

    Resources:
      - S3 (console bucket) + CloudFront distribution with Origin Access Control
      - Amazon Cognito user pool (bootstrap admin user)
      - REST API (API Gateway + Lambda) for console reads/actions
      - WebSocket API for live updates fed by DynamoDB Streams

    The console never performs enterprise writes directly: user actions publish events
    (Customer Action Selected, Approval Decided, scenario events) to opsresolve-bus.
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

        # TODO: cloudfront.Distribution over console S3 bucket (OAC)
        # TODO: cognito.UserPool + bootstrap user
        # TODO: apigateway.RestApi + apigatewayv2 WebSocket API (DynamoDB Streams fan-out)

        self.distribution = None
        self.user_pool = None
