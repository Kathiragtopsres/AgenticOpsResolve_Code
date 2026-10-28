"""Foundation stack: -owned foundation resources + imports of team-owned resources.

Ownership model (OPS-001):
  - This stack CREATES resources  owns: an artifacts/reports/RCS-media S3 bucket,
    Secrets Manager secrets, SSM parameters, (if needed) a KMS key for them, and the
    case-store DynamoDB tables (cases, conversations, idempotency — Section 9.1/9.2).
    The case-store tables turned out not to be part of OPS-003's delivery (see note below),
    so they were created directly via CLI this session; CDK should import them by name
    once this stack is actually deployed, not recreate them.
  - This stack IMPORTS (never creates/modifies) everything already owned by other
    workstreams: the 3 ML/KB-owned S3 buckets, the Bedrock Knowledge Base + Guardrail,
    the 2 shared IAM execution roles, and Subha's (OPS-003) mock enterprise data +
    telemetry DynamoDB tables.
  - No new IAM roles are authored here. CDK auto-created service/custom-resource roles
    at deploy time are fine; the 2 shared execution roles below are reused as-is.

Note on OPS-003 scope: Subha's OPS-003 delivered the *mock enterprise data* tables
(opsresolve-dev-mock-*, seeded from opsresolve_build_test_kit/seed/) and the telemetry
latest-state table (opsresolve-dev-telemetry) — these are DynamoDB tables, not S3 buckets,
despite the stale name below. It did not deliver the Section 9.1 case-store tables
(cases/conversations/idempotency); those are runtime-only stores with no seed data, and
were created by us (see resource.md) once that became clear.

Scope: dev environment, Scenario 1 (Cold-Chain Chiller) only.
"""
from __future__ import annotations

from aws_cdk import Stack
from constructs import Construct

# --- Team-owned resource names (imported, never created here) ---------------------------
# S3 buckets owned by ML + Knowledge-Base workstreams.
KNOWLEDGE_BUCKET_NAME = "opsresolve-dev-knowledge"
ML_ARTIFACTS_BUCKET_NAME = "opsresolve-dev-ml-artifacts"

# Telemetry latest-state store (OPS-003/OPS-004, Subha) — this is a DynamoDB table
# (pk=TELEMETRY#<assetId>#<profile>, sk=ts), not an S3 bucket.
TELEMETRY_TABLE_NAME = "opsresolve-dev-telemetry"

# Mock enterprise data tables (OPS-003, Subha) — CRM/EAM/ERP/FSM seed data backing the
# tool adapters in src/opsresolve/tools/*.py. All pk/sk (String/String), PAY_PER_REQUEST,
# pk = "<ENTITY>#<id>" (sk always "META"), except mock-inventory which uses
# pk = "INV#<depotId>#<partId>". Loaded from opsresolve_build_test_kit/seed/.
MOCK_CUSTOMERS_TABLE_NAME = "opsresolve-dev-mock-customers"
MOCK_SITES_TABLE_NAME = "opsresolve-dev-mock-sites"
MOCK_CONTACTS_TABLE_NAME = "opsresolve-dev-mock-contacts"
MOCK_ASSETS_TABLE_NAME = "opsresolve-dev-mock-assets"
MOCK_CONTRACTS_TABLE_NAME = "opsresolve-dev-mock-contracts"
MOCK_DEPOTS_TABLE_NAME = "opsresolve-dev-mock-depots"
MOCK_PARTS_TABLE_NAME = "opsresolve-dev-mock-parts"
MOCK_INVENTORY_TABLE_NAME = "opsresolve-dev-mock-inventory"
MOCK_TECHNICIANS_TABLE_NAME = "opsresolve-dev-mock-technicians"
MOCK_WORK_ORDER_HISTORY_TABLE_NAME = "opsresolve-dev-mock-work-order-history"
MOCK_APPROVERS_TABLE_NAME = "opsresolve-dev-mock-approvers"
MOCK_COURIERS_TABLE_NAME = "opsresolve-dev-mock-couriers"
MOCK_NETWORK_TABLE_NAME = "opsresolve-dev-mock-network"

# Shared IAM execution roles (spelling matches the resources exactly as provisioned).
LAMBDA_EXEC_ROLE_NAME = "opsreslove-lambda-exe-role"
AGENTCORE_EXEC_ROLE_NAME = "opsresolve-agentcore-agent-exe-role"

# Bedrock Knowledge Base + Guardrail (spelling matches the resources exactly as provisioned).
KNOWLEDGE_BASE_NAME = "opsreslove-knowledge-base"
GUARDRAIL_NAME = "opsreslove-guardrail"

# Case-store DynamoDB table names (Section 9.1/9.2) — created by us via CLI this session
# (see resource.md). pk/sk single-table design; cases has GSI1 (status board) + GSI2
# (asset history), Streams, PITR; idempotency has TTL on attribute "ttl".
CASES_TABLE_NAME = "opsresolve-dev-cases"
CONVERSATIONS_TABLE_NAME = "opsresolve-dev-conversations"
IDEMPOTENCY_TABLE_NAME = "opsresolve-dev-idempotency"


class FoundationStack(Stack):
    """Cross-cutting foundation resources: -owned creates + team-owned imports.

    Creates (-owned):
      - S3 bucket opsresolve-<env>-agent-artifacts (reports, RCS/WhatsApp media, generated
        attachments produced by the agent runtime and channel adapters)
      - Secrets Manager: postback HMAC key, WhatsApp Flow endpoint keys
      - SSM parameters: model IDs (/opsresolve/<env>/models/*), demo clock factor,
        prompts version, feature flags
      - KMS key for the above, only if a customer-managed key is actually required
        (S3/Secrets/SSM SecureString default to AWS-managed keys otherwise)

    Now also creates (added once it became clear OPS-003 didn't cover these):
      - DynamoDB: opsresolve-dev-cases, opsresolve-dev-conversations, opsresolve-dev-idempotency
        (created directly via CLI this session — see resource.md; CDK should import by name,
        not recreate)

    Imports (reference only, never created/modified here):
      - S3: opsresolve-dev-knowledge, opsresolve-dev-ml-artifacts
      - IAM roles: opsreslove-lambda-exe-role, opsresolve-agentcore-agent-exe-role
      - Bedrock Knowledge Base: opsreslove-knowledge-base; Guardrail: opsreslove-guardrail
      - DynamoDB (OPS-003/Subha): opsresolve-dev-telemetry + the 13 opsresolve-dev-mock-*
        tables (mock CRM/EAM/ERP/FSM seed data)
    """

    def __init__(self, scope: Construct, construct_id: str, *, env_name: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.env_name = env_name
        self.prefix = f"opsresolve-{env_name}"

        # --- -owned resources (create) ------------------------------------------
        # TODO: kms.Key(self, "AgentKmsKey", enable_key_rotation=True)  # only if required
        # TODO: s3.Bucket(self, "AgentArtifactsBucket",
        #        bucket_name=f"{self.prefix}-agent-artifacts",
        #        versioned=True, block_public_access=BLOCK_ALL, encryption=KMS/S3_MANAGED)
        # TODO: secretsmanager.Secret(self, "PostbackHmacKey", ...)
        # TODO: secretsmanager.Secret(self, "WhatsAppFlowKeys", ...)
        # TODO: ssm.StringParameter for each key in config/ssm_parameters.json
        #        (/{self.prefix}/models/reasoning, /models/lightweight, /demoClockFactor,
        #         /prompts/version, /flags/WHATSAPP_ENABLED)

        self.kms_key = None
        self.agent_artifacts_bucket = None
        self.postback_hmac_secret = None
        self.whatsapp_flow_secret = None

        # --- Case-store resources (create — CLI-created this session, CDK should import) -
        # TODO: self.cases_table = dynamodb.Table.from_table_attributes(self, "CasesTable",
        #        table_name=CASES_TABLE_NAME,
        #        global_indexes=["GSI1", "GSI2"])  # already exists; do not recreate
        # TODO: self.conversations_table = dynamodb.Table.from_table_name(self, "ConversationsTable", CONVERSATIONS_TABLE_NAME)
        # TODO: self.idempotency_table = dynamodb.Table.from_table_name(self, "IdempotencyTable", IDEMPOTENCY_TABLE_NAME)

        self.cases_table = None
        self.conversations_table = None
        self.idempotency_table = None

        # --- Team-owned resources (import, never create/modify) -------------------------
        # TODO: self.knowledge_bucket = s3.Bucket.from_bucket_name(self, "KnowledgeBucket", KNOWLEDGE_BUCKET_NAME)
        # TODO: self.ml_artifacts_bucket = s3.Bucket.from_bucket_name(self, "MlArtifactsBucket", ML_ARTIFACTS_BUCKET_NAME)
        # TODO: self.lambda_exec_role = iam.Role.from_role_name(self, "LambdaExecRole", LAMBDA_EXEC_ROLE_NAME)
        # TODO: self.agentcore_exec_role = iam.Role.from_role_name(self, "AgentCoreExecRole", AGENTCORE_EXEC_ROLE_NAME)
        # TODO: self.telemetry_table = dynamodb.Table.from_table_name(self, "TelemetryTable", TELEMETRY_TABLE_NAME)
        # TODO: self.mock_tables = {name: dynamodb.Table.from_table_name(self, f"Mock{name.title()}", tbl)
        #        for name, tbl in {
        #            "customers": MOCK_CUSTOMERS_TABLE_NAME, "sites": MOCK_SITES_TABLE_NAME,
        #            "contacts": MOCK_CONTACTS_TABLE_NAME, "assets": MOCK_ASSETS_TABLE_NAME,
        #            "contracts": MOCK_CONTRACTS_TABLE_NAME, "depots": MOCK_DEPOTS_TABLE_NAME,
        #            "parts": MOCK_PARTS_TABLE_NAME, "inventory": MOCK_INVENTORY_TABLE_NAME,
        #            "technicians": MOCK_TECHNICIANS_TABLE_NAME,
        #            "work_order_history": MOCK_WORK_ORDER_HISTORY_TABLE_NAME,
        #            "approvers": MOCK_APPROVERS_TABLE_NAME, "couriers": MOCK_COURIERS_TABLE_NAME,
        #            "network": MOCK_NETWORK_TABLE_NAME,
        #        }.items()}
        # NOTE: Knowledge Base and Guardrail don't have first-class CDK L2 "from_*" lookups;
        #       reference by ID/ARN looked up via SSM/context or a custom resource read, and
        #       pass the raw ID string to AgentCore Gateway/Runtime config in PlatformStack.

        self.knowledge_bucket = None
        self.ml_artifacts_bucket = None
        self.lambda_exec_role = None
        self.agentcore_exec_role = None
        self.telemetry_table = None
        self.mock_tables: dict[str, object] = {}
        self.knowledge_base_id = KNOWLEDGE_BASE_NAME
        self.guardrail_id = GUARDRAIL_NAME
