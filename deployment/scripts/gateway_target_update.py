"""Add the Scenario 2 tools to the AgentCore Gateway target's tool schema (applyServiceCredit, dispatchCourier).

Reads the live target (read-only), writes a backup and the update request, and prints the update command; with
``--apply`` it runs it. Existing tools are kept as they are, except that ``sendCustomerMessage`` ``data.options``
items also list ``optionId`` / ``label`` / ``intent`` (the fields the planner really sends). Re-running adds nothing
twice.

  python scripts/gateway_target_update.py --out <dir>            # prepare only
  python scripts/gateway_target_update.py --out <dir> --apply
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

GATEWAY_ID, TARGET_ID = "opsresolve-gateway-4pqnd9tdmj", "MHLA1ND4PO"

NEW_TOOLS = [
    {
        "name": "applyServiceCredit",
        "description": ("Applies a downtime service credit (USD) to the customer's account and records it on the case. "
                        "Call only for an amount up to the tenant's auto-apply cap; above it an approval is needed first."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "caseId": {"type": "string", "description": "The service case the credit belongs to."},
                "customerId": {"type": "string", "description": "Customer id, CUST-xxxx."},
                "amountUsd": {"type": "number", "description": "Credit amount in USD, a number (e.g. 30)."},
                "reason": {"type": "string", "description": "Short reason, e.g. '6 h of downtime'."},
                "idempotencyKey": {"type": "string", "description": "Unique key for idempotent execution."},
                "correlationId": {"type": "string", "description": "Optional trace correlation ID."},
            },
            "required": ["caseId", "customerId", "amountUsd", "idempotencyKey"],
        },
    },
    {
        "name": "dispatchCourier",
        "description": ("Dispatches a courier from a hub with a customer-replaceable part for self-install. Sends the "
                        "customer a WhatsApp ETA with a handover code and emails the self-install guide."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "caseId": {"type": "string", "description": "The service case."},
                "partId": {"type": "string", "description": "Part to deliver, e.g. ONT-X200."},
                "fromDepotId": {"type": "string", "description": "Courier hub the part is reserved at, e.g. HUB-CHN-C1."},
                "contactId": {"type": "string", "description": "Customer contact id, CON-xxxx (optional)."},
                "idempotencyKey": {"type": "string", "description": "Unique key for idempotent execution."},
                "correlationId": {"type": "string", "description": "Optional trace correlation ID."},
            },
            "required": ["caseId", "partId", "fromDepotId", "idempotencyKey"],
        },
    },
]

OPTION_PROPS = {
    "optionId": {"type": "string", "description": "Option id returned when tapped (e.g. COURIER, TECH_TOMORROW)."},
    "label": {"type": "string", "description": "Button title, at most 20 characters."},
    "intent": {"type": "string", "description": "Optional intent the tap sends back (e.g. SELF_INSTALL_DONE)."},
}


def _aws(*args: str) -> dict:
    out = subprocess.run(["aws", "bedrock-agentcore-control", *args, "--region", "us-east-1", "--output", "json"],
                         check=True, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    return json.loads(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    live = _aws("get-gateway-target", "--gateway-identifier", GATEWAY_ID, "--target-id", TARGET_ID)
    (args.out / "gateway-target-before.json").write_text(json.dumps(live, indent=2), encoding="utf-8")

    config = json.loads(json.dumps(live["targetConfiguration"]))
    tools = config["mcp"]["lambda"]["toolSchema"]["inlinePayload"]
    names = {t["name"] for t in tools}
    added = [t["name"] for t in NEW_TOOLS if t["name"] not in names]
    tools += [t for t in NEW_TOOLS if t["name"] not in names]
    send = next(t for t in tools if t["name"] == "sendCustomerMessage")
    items = send["inputSchema"]["properties"]["data"]["properties"]["options"]["items"]
    for key, spec in OPTION_PROPS.items():
        items.setdefault("properties", {}).setdefault(key, spec)

    request = {"gatewayIdentifier": GATEWAY_ID, "targetId": TARGET_ID, "name": live["name"],
               "targetConfiguration": config, "credentialProviderConfigurations": live["credentialProviderConfigurations"]}
    if live.get("description"):
        request["description"] = live["description"]
    rollback = {"gatewayIdentifier": GATEWAY_ID, "targetId": TARGET_ID, "name": live["name"],
                "targetConfiguration": live["targetConfiguration"],
                "credentialProviderConfigurations": live["credentialProviderConfigurations"]}
    if live.get("description"):
        rollback["description"] = live["description"]
    rollback_path = args.out / "gateway-target-rollback.json"
    rollback_path.write_text(json.dumps(rollback, indent=2), encoding="utf-8")
    path = args.out / "gateway-target-update.json"
    path.write_text(json.dumps(request, indent=2), encoding="utf-8")
    print(f"live tools: {sorted(names)}\nadding: {added or 'nothing'}; options items now list {sorted(items['properties'])}")
    cmd = ["aws", "bedrock-agentcore-control", "update-gateway-target", "--region", "us-east-1",
           "--cli-input-json", f"file://{path.resolve().as_posix()}"]
    print(f"rollback: aws bedrock-agentcore-control update-gateway-target --region us-east-1 "
          f"--cli-input-json file://{rollback_path.resolve().as_posix()}")
    print("apply:", " ".join(cmd))
    if args.apply:
        subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
