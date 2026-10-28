AGENTIC OPSRESOLVE 
================================================
Backup date : 2026-10-23
Project root: this directory (/)

OpsResolve is an AWS-native, agentic service-resolution platform. A Supervisor
(orchestrator) agent plus 10 specialist agents (Strands Agents SDK, deployed on
Amazon Bedrock AgentCore Runtime) take a service incident from detection (IoT
telemetry anomaly or a customer message) through diagnosis, remote remediation,
field-service scheduling, proactive service recovery and verified closure, while
holding one two-way customer conversation over RCS / WhatsApp / SMS / email.

All customers, people, phone numbers, assets and documents in this project are
SYNTHETIC demo data.

This copy has been cleaned: build output (build/), dependency caches, logs,
CDK output (cdk.out), temp files and other zip backups are NOT included, and
AWS account IDs, resource IDs, keys, secrets, usernames/passwords and local
paths have been replaced with placeholders such as <AWS_ACCOUNT_ID>,
<USER_POOL_ID>, <POSTBACK_HMAC_KEY>, <PROJECT_ROOT>.  Replace these
placeholders with your own values before deploying.


ARCHITECTURE
------------
  Agentic core   A Supervisor (Orchestrator) with 10 specialist agents exposed as tools,
                 packaged as one Amazon Bedrock AgentCore Runtime agent (opsresolve-agent,
                 Strands Agents SDK, Python 3.12). A deterministic control loop wraps the
                 model-driven planner: locking, state-machine validation, atomic commit and
                 fail-safe escalation.
  Governed tools Business-intent tools behind AgentCore Gateway (MCP) + Identity, authorised
                 by AgentCore Policy (Cedar, policies/) and implemented as Lambda adapters
                 over mock enterprise systems (CRM, EAM, ERP, WFM/FSM, device management).
  Channels       AWS End User Messaging: RCS (rich UI), WhatsApp (conversation + booking
                 flow), SMS (urgent/approvals/fallback); SES for formal email and reports.
  Event fabric   One EventBridge bus (opsresolve-<env>-bus) with archive/replay, plus
                 EventBridge Scheduler timers; IoT Core/SiteWise telemetry ingest.
  State          DynamoDB case store and mock enterprise tables; S3 for knowledge,
                 artifacts and reports.
  APIs           API Gateway + Lambda for the Ops Console REST API (src/opsresolve/console_api)
                 and the field-technician mobile web app (mobile_web_app/lambdas).
  Flow           telemetry anomaly or customer message -> inbound normalizer -> Agent
                 Invoker Lambda -> orchestrator -> specialists/tools -> customer messages ->
                 approvals/field visit -> verified closure.


1. DIRECTORY GUIDE  (paths relative to this directory)
-------------------------------------------------------------------
SOURCE CODE
  src/opsresolve/agents/          Orchestrator, 10 specialist agents, planner, prompts/,
                                  Pydantic output schemas/, AgentCore runtime app, gateway client
  src/opsresolve/tools/           Business-intent tool adapters (case, scheduling, parts, ...)
                                  + JSON tool schemas
  src/opsresolve/channels/        RCS, SMS, WhatsApp, SES senders and channel router
  src/opsresolve/inbound/         Inbound normalizer, postback signer/verifier
  src/opsresolve/engines/         Slot engine, technician scoring, recovery-risk engine
  src/opsresolve/runtime/         Agent Invoker Lambda handler, SiteWise anomaly bridge
  src/opsresolve/console_api/     Ops Console REST API Lambdas (read, approval, scenario control)
  src/opsresolve/notifications/   Important-update e-mail notification logic
  src/opsresolve/mocks/           Simulated enterprise systems (CRM, EAM, ERP, WFM/FSM, device mgmt)
  src/opsresolve/common/          Shared models, events, config/resource naming, case store, logging
  src/gateway tools/              AgentCore Gateway target Lambda (lambda_handler.py). Contains the code for AWS Communication Developer Services - Whatsapp, RCS, SMS, SES
  src/mobile_web_app/lambdas/     Field-technician mobile web app backend Lambdas (4 bundles)

UI
  ui/web_console                  Web application React UI Application Code
  ui/mobile_web_app               Mobile Field Engineer Application Code
  
DEPLOYMENT
  infra/                          AWS CDK v2 (Python): stacks/ (Foundation, Platform, Experience),
                                  plus IAM policy JSON and command runbooks in console_api/,
                                  inbound/, mobile/, whatsapp/
  policies/                       Cedar policies (opsresolve.cedar) + fixtures
  config/                         demo.yaml, ssm_parameters.json, secrets.json (template)
  src/config/                     Copy of demo.yaml + ssm_parameters.json - REQUIRED: the
                                  AgentCore runtime zips src/ as its root. Keep both in sync.
  schemas/                        Shared JSON Schemas (events, case, channels, tools, errors)
  agentcore/                      AgentCore CLI project (agentcore.json) and its CDK app (cdk/)
  scripts/                        Deploy, seed, reset, rehearse, preflight, packaging, validation
  samples/inputs,                 samples/outputs Sample events/payloads and expected outputs

SCENARIOS
  Scenario/                         Golden end-to-end results per scenario
  Scenario/knowledge-data/               Knowledge-base documents (troubleshooting guides, SOPs, policies
  Scenario/s1-cold-chain-chiller/     Scenario 1 data (customers, assets, sites, contracts,
                                  technicians, parts, inventory, approvers, work-order history)
  Scenario/s2-broadband-whatsapp/     Scenario 2 data (adds couriers, network)


TESTS
  tests/unit, contract, e2e, eval, load, security   (+ test_catalogue.csv / .xlsx)
  telemetry/                      Telemetry profiles (CSV), IoT rule SQL, telemetry_sim.py

DOCUMENTATION
  infra/README.txt, opsresolve/README.txt,
  mobile_web_app/README.txt, tests/README.txt   Per-area notes


2. PREREQUISITES
----------------
  - Python 3.12 or newer
  - Node.js 18+ and npm (only for opsresolve/agentcore/cdk/)
  - AWS CLI configured (only for deploying; local run/tests need no AWS account)
  - AWS CDK v2 CLI (only to deploy infra/)
  - For a real deployment: an AWS account in us-east-1 with Amazon Bedrock model access,
    AgentCore, IoT SiteWise/IoT Core, DynamoDB, EventBridge, Lambda, Secrets Manager,
    End User Messaging (RCS/SMS/WhatsApp) and SES.


3. LOCAL SETUP (NO AWS ACCOUNT NEEDED)
--------------------------------------
  # Windows PowerShell
  python -m venv .venv
  .venv\Scripts\Activate.ps1
  # macOS / Linux
  # python -m venv .venv && source .venv/bin/activate

  pip install -e ".[dev]"

  # Run the unit tests
  python -m pytest -q tests/unit

  # Validate the kit (schemas, seed data, samples, postback signatures)
  python scripts/validate_kit.py

  # Dry-run the telemetry simulator and the seed loader
  python telemetry/telemetry_sim.py telemetry/profiles/s1_chiller_degrading.csv --dry-run --speed 6000
  python scripts/load_seed.py --scenario s1-cold-chain-chiller --dry-run

Notes:
  - tests run with pythonpath = src + reference (configured in pyproject.toml).
  - Optional dependency groups: ".[infra]" (aws-cdk-lib, constructs, cdk-nag).


4. CONFIGURATION AND SECRETS
----------------------------
  - Copy/edit config/secrets.template.json to config/secrets.json (gitignored; never commit).
    Generate the postback HMAC key (32 random bytes, base64) and store it in AWS Secrets
    Manager as opsresolve/<env>/postback-hmac-key. Create console users in Cognito instead of
    storing passwords in files.
  - The key 'demo-hmac-key-rotate-before-use' used in samples/ and tests is a demo value only.
  - Replace the <PLACEHOLDER> values found in infra/**/*.json, infra/**/commands.txt,
    gateway tools/lambda_handler.py defaults
    (account ID, Cognito pool/client IDs, API ID, gateway ID, WhatsApp/RCS IDs,
    Bedrock guardrail and knowledge-base IDs) with the values from your own account.
    (grep -r "<[A-Z_]*>" to find them.)
  - config/ssm_parameters.json holds model IDs and runtime parameters.


5. DEPLOYING TO AWS (OVERVIEW)
------------------------------
  1. Infrastructure:   cd infra ; pip install -r requirements.txt ; cdk synth ; cdk deploy --all
                       (stacks: Foundation, Platform, Experience).  Step-by-step AWS CLI
                       runbooks for the console API, inbound normalizer, mobile APIs and
                       WhatsApp are in infra/console_api/commands.txt, infra/inbound/commands.txt
                       and infra/README.txt.
  2. Lambda packages:  scripts/package_agent_invoker.py, scripts/package_gateway_tools.py,
                       scripts/package_console_api.ps1, scripts/package_inbound_normalizer.ps1
                       (these create the build/ output, which is not included in this backup).
  3. Agent runtime:    cd opsresolve/agentcore ; npm install (in cdk/) ; deploy with the
                       AgentCore CLI using agentcore.json.  See opsresolve/README.txt.
  4. Seed data:        python scripts/seed.py --env <env>
                       (or scripts/load_seed.py --scenario <name>; seed_console_demo.py and
                       seed_console_mock_extras.py load console demo data)
  5. Knowledge base:   python scripts/ingest_knowledge.py   (uploads knowledge/ documents)
  6. Verify:           python scripts/preflight.py --env <env>
                       python scripts/rehearse.py   (end-to-end scenario rehearsal)
                       python scripts/reset_demo.py (reset demo state between runs)
  7. Ops Console:      not included in this backup (placeholder removed; the console UI is
                       hosted separately)


6. SCENARIOS
------------
  S1 Cold-Chain Chiller (CX-500)   - telemetry anomaly, remote fix attempt, parts + technician
  S2 Broadband / WhatsApp (ONT-X200) - customer-initiated WhatsApp conversation, service credit

7. Try out Links
-------------------------------------------
Desktop App URL : Link Provided in the 'Tryout Link section' of the Hackathon submission.

Username: John_doe
Password: User#001

Mobile App URL : Link Provided in the 'Tryout Link section' of the Hackathon submission.
Username: ryan_kumar
Password: User#001


