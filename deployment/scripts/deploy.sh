#!/usr/bin/env bash
# Deploy all CDK stacks for an environment (Section 15.4).
#
# Usage: ./scripts/deploy.sh <env>        # env = dev | demo
set -euo pipefail

ENV="${1:-dev}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Deploying TCS Agentic OpsResolve to env '${ENV}'"

cd "${ROOT}/infra"
pip install -r requirements.txt >/dev/null

# Synthesize then deploy all stacks in dependency order.
cdk synth -c "env=${ENV}" >/dev/null
cdk deploy --all -c "env=${ENV}" --require-approval broadening

echo "==> Deploy complete. Next: python scripts/seed.py --env ${ENV} && python scripts/preflight.py --env ${ENV}"
