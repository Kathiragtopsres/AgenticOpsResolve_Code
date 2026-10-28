
# infra/ — AWS CDK v2 (Python)

Infrastructure as code for TCS Agentic OpsResolve, scoped to **tasks**
(OPS-001/002/005/006/007/010/011/012/013) and **Scenario 1 (Cold-Chain Chiller, CX-500)**
only, **dev environment first** (no CI/CD, no `cdk deploy` from this session — manual
deploy steps live in `../deploy.md`).

## Ownership model

This app **creates only resources  owns**. Everything already owned by another
workstream is **imported by name/ARN**, never created, deployed, or modified here:

| Resource | Owner | How it's used here |
|---|---|---|
| S3 `opsresolve-dev-knowledge`, `opsresolve-dev-telemetry`, `opsresolve-dev-ml-artifacts` | ML + KB team | Imported in `FoundationStack` via `from_bucket_name` |
| IAM `opsreslove-lambda-exe-role` | Platform-wide | Imported; reused by every Lambda in `PlatformStack` |
| IAM `opsresolve-agentcore-agent-exe-role` | Platform-wide | Imported; used by the AgentCore Runtime registration |
| Bedrock Knowledge Base `opsreslove-knowledge-base` | KB team | Referenced by ID in `FoundationStack`, consumed in `PlatformStack` |
| Bedrock Guardrail `opsreslove-guardrail` | KB team | Referenced by ID in `FoundationStack` |
| DynamoDB cases/conversations/idempotency tables | Subha (OPS-003) | Imported in `FoundationStack` via `from_table_name` (names are placeholders pending confirmation — see `foundation_stack.py`) |
| Anomaly detection model (IoT stream consumer) | Subha (OPS-004) | Not built here; `PlatformStack`'s IoT rule stops at an integration seam |

No new IAM roles are authored in this app. CDK-auto-created service/custom-resource roles
at deploy time are fine and expected.

## Stacks

Consolidated from an earlier 8-stack draft down to **3 stacks** (OPS-001):

| Stack | File | Resources (-owned creates) |
|---|---|---|
| Foundation | `stacks/foundation_stack.py` | Agent artifacts S3 bucket (reports/RCS+WhatsApp media), Secrets (postback HMAC, WhatsApp Flow keys), SSM params (model IDs, demo clock, flags), KMS key if required. Also holds the import references listed above. |
| Platform | `stacks/platform_stack.py` | EventBridge bus/rules/archive, Scheduler group, IoT topic rule (telemetry ingest only — anomaly detection is Subha's OPS-004 and attaches downstream), Lambda tool adapters (`src/opsresolve/tools/*`), mock enterprise APIs (`src/opsresolve/mocks/*`: crm, eam, erp, fsm for S1), AgentCore Runtime/Gateway/Policy/Identity/Memory + Agent Invoker Lambda |
| Experience | `stacks/experience_stack.py` | SNS topics, SQS FIFO + DLQ, SES v2 identity/configuration set/templates, CloudWatch dashboards + alarms |

`stacks/console_stack.py` is **left in place but intentionally unwired** (not imported by
`app.py`). The Ops Console is OPS-014, owned by Subha, and out of scope for this
consolidation. It was patched only to remove a now-broken import after the old
`data_stack.py` was merged away — no console functionality was added.

## Usage

```bash
pip install -r requirements.txt
cdk synth -c env=dev
```

`cdk deploy` is intentionally not run from an agent session for this project. See
`../deploy.md` for the manual deploy procedure, prerequisites, and post-deploy seed steps.
