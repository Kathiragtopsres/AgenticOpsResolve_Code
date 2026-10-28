"""
OpsResolve Gateway Lambda  (RCS + WhatsApp)
Single entry point for all 13 gateway tools exposed via AgentCore Gateway.
Dispatch contract: { "tool_name": "...", "arguments": { ... } }

Original code is kept in lambda_handler_backup.py. Changes vs. the original:

WhatsApp (AWS End User Messaging Social, WABA waba-4fba8293f662494ca0a1335bce91f6a6):
  - sendCustomerMessage delivers WHATSAPP for real through SendWhatsAppMessage. The only gate is the recipient
    allowlist (Secrets Manager WA_ALLOWLIST_SECRET_ID, {"numbers": ["+91..."]}); other numbers are recorded in
    DynamoDB only (deliveryMode SIMULATED), same as before.
  - Inside the 24h customer-service window (last inbound from that number, tracked by the inbound normalizer in the
    conversations table under pk="WAUSER#<sha256(digits)[:24]>", sk="WINDOW") it sends free-form text / reply buttons
    / list messages; outside it, the matching APPROVED template (opsresolve_*). No approved template -> DynamoDB only.
  - createServiceAppointment notifies the assigned technician; reassignTechnician notifies the released and the new
    technician (phone from mock-technicians). Tool input schemas are unchanged.
  - All times in messages are UTC (project decision 2026-10-13).

Technician availability (NEXT SESSION B, 2026-10-13):
  - proposeAppointmentSlots reads AVAILABLE, future 2 h windows from opsresolve-dev-field-availability for the given
    technicians (jobs > 120 min take 2 back-to-back windows on the same day; max 240 min) and ranks them SLA-first
    (same rule as src/opsresolve/engines/slot_engine.propose_slots).
  - createServiceAppointment books the covering window(s) in one conditional transaction (no double booking ->
    CONFLICT_STATE), records them on the appointment row (availabilitySlots) and writes the mobile job
    APPT#<workOrderId> into the cases table. reassignTechnician moves the booking; updateWorkOrder status=CANCELLED
    frees it.
  - createServiceAppointment copies the DiagnosticAgent's META.fieldWork (checklist, brief, manualRef) onto the mobile
    job APPT# item (generic default checklist if absent); findAndReservePart sets META.requiredPart (the agent's part,
    not a technician request) when the case has none.
  - findAndReservePart reads stock from opsresolve-dev-mock-inventory (onHand - reserved, never written) and the value
    from mock-parts (unitValueUsd). Optional approvalId: an APPROVED approval for this case and part lifts the
    auto-approve threshold; a technician part request (requestedBy) reserves at the technician's home depot, or at
    another depot plus a transfer, and WhatsApps the technician.
  - executeSafeRemoteAction (NEXT SESSION 3, 2026-10-15): besides the 3 safe chiller actions, CRYO_COMPRESSOR_RESTART
    (regulated imaging) runs only with an approvalId that is APPROVED for the same case and the exact action
    EXECUTE_SAFE_REMOTE_ACTION:<action>; otherwise POLICY_DENIED. The orchestrator also gates it in code.
  - Every button carries a signed postback (same format as RCS). Each real send writes a pointer item
    pk="WAMSG#<wamid>", sk="META" so the normalizer can attach delivery statuses to the message record.
  - Phone numbers are never logged (mask_phone keeps the last 2 digits) and never returned to the caller.

RCS changes (sendCustomerMessage, AWS End User Messaging SMS/RCS):
  1. Contact lookup fixed: mock-contacts is keyed pk="CONTACTS#<id>", sk="META" (was pk=<id>, sk="CONTACT",
     which always missed, so consent defaulted to "all opted in" and RCS was never preferred).
  2. Conversation records are keyed by case: pk="CASE#<caseId>", sk="MSG#<ts>#<messageId>"
     (was pk=<messageId>, which forced a scan to list a case's messages).
  3. RCS messages are built as rich cards / carousels / suggestion buttons. Every button carries a signed
     postback ("opsr1|...|hmac"), the same format as src/opsresolve/inbound/postback.py, so the inbound
     normalizer can verify taps.
  4. Real SendRcsMessage call, only when RCS_SEND_ENABLED=true (default false = DynamoDB record only).
     No SMS fallback is configured.

Handler: lambda_handler.lambda_handler
Env vars: RCS_SEND_ENABLED, RCS_AGENT_ID, POSTBACK_KEY_SECRET_ID, WA_PHONE_NUMBER_ID, WA_WABA_ID, WA_META_API_VERSION,
          WA_ALLOWLIST_SECRET_ID, WA_TEMPLATE_LANGUAGE
IAM: infra/inbound/policy-gateway-tools.json
"""

import json
import os
import re
import uuid
import hashlib
import hmac
import logging
import secrets as pysecrets
import time
from decimal import Decimal
from datetime import datetime, timezone, timedelta

import boto3
from botocore.exceptions import ClientError, ParamValidationError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# AWS clients (lazy-initialised so cold start is fast)
# ---------------------------------------------------------------------------
_dynamodb = None
_s3 = None
_bedrock_agent_runtime = None
_ses = None
_sms_voice = None
_secrets_client = None
_postback_key_cache = None
_social = None
_ses = None

def get_social():
    global _social
    if _social is None:
        _social = boto3.client("socialmessaging", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    return _social

def get_sms_voice():
    global _sms_voice
    if _sms_voice is None:
        _sms_voice = boto3.client("pinpoint-sms-voice-v2", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    return _sms_voice

def get_secrets_client():
    global _secrets_client
    if _secrets_client is None:
        _secrets_client = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    return _secrets_client

def get_dynamodb():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    return _dynamodb

def get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    return _s3

def get_bedrock_agent_runtime():
    global _bedrock_agent_runtime
    if _bedrock_agent_runtime is None:
        _bedrock_agent_runtime = boto3.client(
            "bedrock-agent-runtime",
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
        )
    return _bedrock_agent_runtime
def get_ses():
    global _ses
    if _ses is None:
        _ses = boto3.client("ses", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    return _ses

# ---------------------------------------------------------------------------
# Environment / config
# ---------------------------------------------------------------------------
TABLE_IDEMPOTENCY    = os.environ.get("TABLE_IDEMPOTENCY",    "opsresolve-dev-idempotency")
TABLE_PARTS          = os.environ.get("TABLE_PARTS",          "opsresolve-dev-mock-parts")
TABLE_RESERVATIONS   = os.environ.get("TABLE_RESERVATIONS",   "opsresolve-dev-reservations")
TABLE_WORK_ORDERS    = os.environ.get("TABLE_WORK_ORDERS",    "opsresolve-dev-mock-work-order-history")
TABLE_CASES          = os.environ.get("TABLE_CASES",          "opsresolve-dev-cases")
TABLE_CONTACTS       = os.environ.get("TABLE_CONTACTS",       "opsresolve-dev-mock-contacts")
TABLE_APPROVALS      = os.environ.get("TABLE_APPROVALS",      "opsresolve-dev-approvals")
TABLE_TRANSFERS      = os.environ.get("TABLE_TRANSFERS",      "opsresolve-dev-transfers")
TABLE_REMOTE_ACTIONS = os.environ.get("TABLE_REMOTE_ACTIONS", "opsresolve-dev-remote-actions")
TABLE_MESSAGES       = os.environ.get("TABLE_MESSAGES",       "opsresolve-dev-conversations")
TABLE_APPOINTMENTS   = os.environ.get("TABLE_APPOINTMENTS",   "opsresolve-dev-appointments")
# Technician availability (mobile app): pk TECHNICIAN#<id>, sk SLOT#<YYYY-MM-DD>#<slotId>, date, start/end "HH:MM" UTC,
# status AVAILABLE | BOOKED, appointmentId. 4 fixed 2 h windows per day (09-11, 11-13, 14-16, 16-18 UTC).
TABLE_FIELD_AVAILABILITY = os.environ.get("TABLE_FIELD_AVAILABILITY", "opsresolve-dev-field-availability")
# Depot stock: pk INV#<depotId>#<partId>, sk META, onHand, reserved. Read only here (never written).
TABLE_INVENTORY      = os.environ.get("TABLE_INVENTORY",      "opsresolve-dev-mock-inventory")
DEFAULT_DEPOT_ID     = os.environ.get("DEFAULT_DEPOT_ID",     "DEP-CHN-01")
BUCKET_REPORTS       = os.environ.get("BUCKET_REPORTS",       "opsreslove-dev-reports")
KNOWLEDGE_BASE_ID    = os.environ.get("KNOWLEDGE_BASE_ID",    "TA0KF90K4I")
AUTO_APPROVE_THRESHOLD_USD = float(os.environ.get("AUTO_APPROVE_THRESHOLD_USD", "500"))
IDEMPOTENCY_TTL_SECONDS    = int(os.environ.get("IDEMPOTENCY_TTL_SECONDS", str(86400 * 7)))  # 7 days

# --- RCS (AWS End User Messaging) ---
# Real sends are OFF by default: with RCS_SEND_ENABLED != "true" the message is only recorded in DynamoDB.
RCS_SEND_ENABLED       = os.environ.get("RCS_SEND_ENABLED", "false").strip().lower() == "true"
RCS_AGENT_ID           = os.environ.get("RCS_AGENT_ID", "rcs-24696a96570f4a60b41ba719424abf83")
POSTBACK_KEY_SECRET_ID = os.environ.get("POSTBACK_KEY_SECRET_ID", "opsresolve/dev/postback-hmac-key")

# --- WhatsApp (AWS End User Messaging Social) ---
TABLE_TECHNICIANS      = os.environ.get("TABLE_TECHNICIANS", "opsresolve-dev-mock-technicians")
TABLE_ASSETS           = os.environ.get("TABLE_ASSETS", "opsresolve-dev-mock-assets")
TABLE_CONTRACTS        = os.environ.get("TABLE_CONTRACTS", "opsresolve-dev-mock-contracts")
TABLE_COURIERS         = os.environ.get("TABLE_COURIERS", "opsresolve-dev-mock-couriers")
TABLE_DEPOTS           = os.environ.get("TABLE_DEPOTS", "opsresolve-dev-mock-depots")
# Broadband line diagnostics (S2): pk NETWORK#ALL, lineProfiles.<assetId>.{active, profiles}.
TABLE_NETWORK          = os.environ.get("TABLE_NETWORK", "opsresolve-dev-mock-network")
WA_PHONE_NUMBER_ID     = os.environ.get("WA_PHONE_NUMBER_ID", "phone-number-id-a49b86fe2faf4f06bd1e26b9b75f3b01")
WA_WABA_ID             = os.environ.get("WA_WABA_ID", "waba-4fba8293f662494ca0a1335bce91f6a6")
WA_META_API_VERSION    = os.environ.get("WA_META_API_VERSION", "v20.0")
WA_ALLOWLIST_SECRET_ID = os.environ.get("WA_ALLOWLIST_SECRET_ID", "opsresolve/dev/whatsapp-allowlist")
WA_TEMPLATE_LANGUAGE   = os.environ.get("WA_TEMPLATE_LANGUAGE", "en")

# ---------------------------------------------------------------------------
# SES config
# ---------------------------------------------------------------------------
SES_SENDER_EMAIL      = os.environ.get("SES_SENDER_EMAIL",      "noreply@opsresolve.io")
SES_REPLY_TO          = os.environ.get("SES_REPLY_TO",           "support@opsresolve.io")
SES_DEFAULT_RECIPIENT = os.environ.get("SES_DEFAULT_RECIPIENT",  "lakshmiprasanna.pallagani@tcs.com")
# Max emails per case per hour — prevents spamming on rapid updates
EMAIL_THROTTLE_MAX    = int(os.environ.get("EMAIL_THROTTLE_MAX", "3"))
EMAIL_THROTTLE_WINDOW = int(os.environ.get("EMAIL_THROTTLE_WINDOW_SECONDS", "600"))  

_DEFAULT_IMPORTANT_TYPES = [
    "OPEN",
    "CLOSED",
    "DIAGNOSING",
    "AWAITING_CUSTOMER",
    "REMOTE_FIX_IN_PROGRESS",
    "SCHEDULED",
    "PLANNING_FIELD",
    "CONTEXT_READY",
    "AWAITING_APPROVAL",
    "SCHEDULED",
    "AT_RISK",
    "IN_SERVICE",
    "VERIFYING",
    "ESCALATED_HUMAN"
]

IMPORTANT_UPDATE_TYPES: set = set(
    json.loads(os.environ["IMPORTANT_UPDATE_TYPES"])
    if "IMPORTANT_UPDATE_TYPES" in os.environ
    else _DEFAULT_IMPORTANT_TYPES
)
# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"

def error_envelope(code: str, message: str, correlation_id: str = None, details: dict = None, retryable: bool = False) -> dict:
    err = {
        "code": code,
        "retryable": retryable,
        "message": message,
        "details": details or {},
        "correlationId": correlation_id or "",
    }
    return {"error": err}

def _idempotency_key_hash(idempotency_key: str, tool_name: str, arguments: dict) -> str:
    """Stable hash over (tool, key, sorted-args) to detect replay with different inputs."""
    payload = json.dumps({"tool": tool_name, "key": idempotency_key, "args": arguments}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()

def check_idempotency(idempotency_key: str, tool_name: str, arguments: dict):
    """
    Returns (True, cached_result) if already processed, else (False, None).
    idempotency table schema: PK=pk, SK=sk
    """
    table = get_dynamodb().Table(TABLE_IDEMPOTENCY)
    digest = _idempotency_key_hash(idempotency_key, tool_name, arguments)
    try:
        resp = table.get_item(Key={"pk": idempotency_key, "sk": tool_name})
        item = resp.get("Item")
        if item:
            if item.get("digest") != digest:
                logger.warning("Idempotency key reused with different inputs: %s", idempotency_key)
            return True, json.loads(item["result"])
    except ClientError as exc:
        logger.error("Idempotency check failed: %s", exc)
    return False, None

def save_idempotency(idempotency_key: str, tool_name: str, arguments: dict, result: dict):
    """
    idempotency table schema: PK=pk, SK=sk
    """
    table = get_dynamodb().Table(TABLE_IDEMPOTENCY)
    digest = _idempotency_key_hash(idempotency_key, tool_name, arguments)
    ttl = int(datetime.now(timezone.utc).timestamp()) + IDEMPOTENCY_TTL_SECONDS
    try:
        table.put_item(Item={
            "pk": idempotency_key,
            "sk": tool_name,
            "digest": digest,
            "result": json.dumps(result),
            "ttl": ttl,
            "createdAt": now_iso(),
        })
    except ClientError as exc:
        logger.error("Failed to save idempotency record: %s", exc)

def validate_required(arguments: dict, fields: list, correlation_id: str = None):
    """Returns an error envelope if any required field is missing, else None."""
    missing = [f for f in fields if not arguments.get(f)]
    if missing:
        return error_envelope(
            "VALIDATION_ERROR",
            f"Missing required field(s): {', '.join(missing)}",
            correlation_id,
        )
    return None

# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

APPROVAL_REQUIRED_ACTIONS = {"CRYO_COMPRESSOR_RESTART"}   # regulated assets: only with an APPROVED approval


LEGACY_SAFE_ACTIONS = {"CONTROLLER_SOFT_RESET", "RECALIBRATE_SENSOR", "SET_ECO_MODE"}


def _asset_safe_actions(asset_id: str) -> set:
    """Actions the asset's mock-assets record lists as safe; the S1 chiller set if it cannot be read."""
    try:
        item = get_dynamodb().Table(TABLE_ASSETS).get_item(
            Key={"pk": f"ASSETS#{asset_id}", "sk": "META"}).get("Item") or {}
    except ClientError as exc:
        logger.error("Asset lookup failed: %s", exc)
        item = {}
    return {str(a) for a in item["safeActions"]} if "safeActions" in item else set(LEGACY_SAFE_ACTIONS)


def _set_post_reboot_line_profile(asset_id: str) -> None:
    """Demo: a rebooted broadband box comes back with partly improved line stats (only if the asset has
    that profile in mock-network; no-op for every other asset)."""
    try:
        get_dynamodb().Table(TABLE_NETWORK).update_item(
            Key={"pk": "NETWORK#ALL", "sk": "META"},
            UpdateExpression="SET lineProfiles.#a.active = :p",
            ConditionExpression="attribute_exists(lineProfiles.#a.profiles.post_reboot_partial)",
            ExpressionAttributeNames={"#a": asset_id},
            ExpressionAttributeValues={":p": "post_reboot_partial"},
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            logger.error("Line profile update failed: %s", exc)


def handle_execute_safe_remote_action(args: dict) -> dict:
    correlation_id = args.get("correlationId")

    err = validate_required(args, ["caseId", "assetId", "action", "idempotencyKey"], correlation_id)
    if err:
        return err

    action = args["action"]
    if action in APPROVAL_REQUIRED_ACTIONS:
        # Human-in-the-loop: runs only with an APPROVED approval for this case and this exact action.
        if not args.get("approvalId"):
            return error_envelope(
                "POLICY_DENIED", f"Action '{action}' needs an approved approvalId", correlation_id)
        try:
            approval = get_dynamodb().Table(TABLE_APPROVALS).get_item(
                Key={"approvalId": args["approvalId"]}).get("Item")
        except ClientError as exc:
            logger.error("Approval lookup failed: %s", exc)
            return error_envelope("DOWNSTREAM_TIMEOUT", "Approvals unavailable", correlation_id, retryable=True)
        if not approval:
            return error_envelope("NOT_FOUND", f"Approval {args['approvalId']} not found", correlation_id)
        if (approval.get("status") != "APPROVED" or approval.get("caseId") != args["caseId"]
                or approval.get("action") != f"EXECUTE_SAFE_REMOTE_ACTION:{action}"):
            return error_envelope(
                "POLICY_DENIED",
                f"Approval {args['approvalId']} is not an APPROVED approval for {action} on case {args['caseId']}",
                correlation_id,
                details={"approvalStatus": approval.get("status"), "action": approval.get("action")},
            )
    elif action not in (allowed := _asset_safe_actions(args["assetId"])):
        return error_envelope(
            "VALIDATION_ERROR",
            f"Action '{action}' is not in the allow-list. Permitted: {sorted(allowed)}",
            correlation_id,
        )

    hit, cached = check_idempotency(args["idempotencyKey"], "executeSafeRemoteAction", args)
    if hit:
        return cached

    # Simulate device management: store record and return success
    action_id = new_id("act-")
    executed_at = now_iso()
    result = {
        "actionId": action_id,
        "status": "EXECUTED",
        "executedAt": executed_at,
        "resultDetail": {"action": action, "assetId": args["assetId"], "caseId": args["caseId"]},
    }

    table = get_dynamodb().Table(TABLE_REMOTE_ACTIONS)
    table.put_item(Item={
        "actionId": action_id,
        "caseId": args["caseId"],
        "assetId": args["assetId"],
        "action": action,
        "status": "EXECUTED",
        "executedAt": executed_at,
        "idempotencyKey": args["idempotencyKey"],
    })

    if action == "ONT_REBOOT":
        _set_post_reboot_line_profile(args["assetId"])
    save_idempotency(args["idempotencyKey"], "executeSafeRemoteAction", args, result)
    return result


def handle_generate_service_report(args: dict) -> dict:
    correlation_id = args.get("correlationId")

    err = validate_required(args, ["caseId", "format", "idempotencyKey"], correlation_id)
    if err:
        return err

    fmt = args["format"]
    if fmt not in ("HTML", "PDF"):
        return error_envelope("VALIDATION_ERROR", "format must be HTML or PDF", correlation_id)

    hit, cached = check_idempotency(args["idempotencyKey"], "generateServiceReport", args)
    if hit:
        return cached

    case_id = args["caseId"]
    generated_at = now_iso()
    key = f"reports/{case_id}/{args['idempotencyKey']}.{fmt.lower()}"

    # Generate minimal report content and upload to S3
    content = f"<html><body><h1>Service Report</h1><p>Case: {case_id}</p><p>Generated: {generated_at}</p></body></html>"
    if fmt == "PDF":
        content = f"%PDF-1.4 (stub) Case:{case_id} Generated:{generated_at}"

    try:
        get_s3().put_object(
            Bucket=BUCKET_REPORTS,
            Key=key,
            Body=content.encode(),
            ContentType="application/pdf" if fmt == "PDF" else "text/html",
        )
        report_url = f"https://{BUCKET_REPORTS}.s3.amazonaws.com/{key}"
    except ClientError as exc:
        logger.error("S3 upload failed: %s", exc)
        return error_envelope("DOWNSTREAM_TIMEOUT", "Report storage unavailable", correlation_id, retryable=True)

    result = {"reportUrl": report_url, "format": fmt, "generatedAt": generated_at}
    save_idempotency(args["idempotencyKey"], "generateServiceReport", args, result)
    return result


def handle_close_service_case(args: dict) -> dict:
    correlation_id = args.get("correlationId")

    err = validate_required(args, ["caseId", "verificationResult", "idempotencyKey"], correlation_id)
    if err:
        return err

    if args["verificationResult"] not in ("PASSED", "FAILED"):
        return error_envelope("VALIDATION_ERROR", "verificationResult must be PASSED or FAILED", correlation_id)

    hit, cached = check_idempotency(args["idempotencyKey"], "closeServiceCase", args)
    if hit:
        return cached

    case_id = args["caseId"]
    work_order_id = args.get("workOrderId")
    closed_at = now_iso()

    if args["verificationResult"] == "FAILED":
        return error_envelope("CONFLICT_STATE", "verificationResult is FAILED: the case stays open", correlation_id)

    # Case store layout (case_store.py): pk=CASE#<id>, sk=META, attribute ``state`` (not ``status``),
    # GSI1 keyed on STATE#<state>. CLOSED is only legal from VERIFYING or ESCALATED_HUMAN (state machine).
    cases_table = get_dynamodb().Table(TABLE_CASES)
    try:
        cases_table.update_item(
            Key={"pk": f"CASE#{case_id}", "sk": "META"},
            UpdateExpression="SET #s = :closed, closedAt = :t, gsi1pk = :g, version = version + :one",
            ConditionExpression="attribute_exists(pk) AND #s IN (:verifying, :escalated)",
            ExpressionAttributeNames={"#s": "state"},
            ExpressionAttributeValues={":closed": "CLOSED", ":t": closed_at, ":g": "STATE#CLOSED", ":one": 1,
                                       ":verifying": "VERIFYING", ":escalated": "ESCALATED_HUMAN"},
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return error_envelope(
                "CONFLICT_STATE", f"Case {case_id} does not exist or is not in VERIFYING/ESCALATED_HUMAN",
                correlation_id)
        raise

    result = {
        "caseId": case_id,
        "closedAt": closed_at,
        "status": "CLOSED",
    }
    if work_order_id:
        result["workOrderId"] = work_order_id

    save_idempotency(args["idempotencyKey"], "closeServiceCase", args, result)
    return result


def _kb_retrieve(query: str, top_k: int) -> dict:
    """Retrieve from the knowledge base. KB TA0KF90K4I is a MANAGED knowledge base, which rejects
    ``vectorSearchConfiguration`` ("Incompatible configuration ... use managedSearchConfiguration"), so
    try the managed shape first and fall back to the vector one (classic KBs, or an older boto3 that
    does not know the managed parameter)."""
    search = {"numberOfResults": top_k, "filter": {"equals": {"key": "docStatus", "value": "ACTIVE"}}}
    client = get_bedrock_agent_runtime()
    base = {"knowledgeBaseId": KNOWLEDGE_BASE_ID, "retrievalQuery": {"text": query}}
    try:
        return client.retrieve(**base, retrievalConfiguration={"managedSearchConfiguration": search})
    except ParamValidationError:
        pass  # boto3 in this runtime predates managed knowledge bases
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ValidationException" or "managed" not in str(exc).lower():
            raise
    return client.retrieve(**base, retrievalConfiguration={"vectorSearchConfiguration": search})


def handle_retrieve_troubleshooting_knowledge(args: dict) -> dict:
    correlation_id = args.get("correlationId")

    err = validate_required(args, ["query"], correlation_id)
    if err:
        return err

    query = args["query"]
    top_k = int(args.get("topK", 6))
    asset_model = args.get("assetModel")
    firmware = args.get("firmware")

    if not KNOWLEDGE_BASE_ID:
        # Return stub chunks when KB is not configured
        chunks = [
            {
                "chunkId": f"stub-chunk-{i}",
                "text": f"Stub troubleshooting chunk {i} for query: {query}",
                "sourceDoc": f"doc-{i:03d}.pdf",
                "score": round(0.95 - i * 0.05, 2),
            }
            for i in range(min(top_k, 3))
        ]
        return {"chunks": chunks}

    try:
        resp = _kb_retrieve(query, top_k)
        chunks = []
        for r in resp.get("retrievalResults", []):
            chunks.append({
                "chunkId": r.get("location", {}).get("s3Location", {}).get("uri", new_id("chunk-")),
                "text": r.get("content", {}).get("text", ""),
                "sourceDoc": r.get("location", {}).get("s3Location", {}).get("uri", ""),
                "score": r.get("score", 0.0),
            })
        return {"chunks": chunks}

    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("ThrottlingException", "TooManyRequestsException"):
            return error_envelope("THROTTLED", "Knowledge base throttled", correlation_id, retryable=True)
        return error_envelope("DOWNSTREAM_TIMEOUT", f"Knowledge base error: {code}", correlation_id, retryable=True)


def _courier_depot_ids() -> set:
    """Depots flagged ``courier`` (customer self-install hubs): a technician's part is never picked there while
    another depot has stock. Empty when the depots table cannot be read."""
    try:
        rows = get_dynamodb().Table(TABLE_DEPOTS).scan().get("Items", [])
    except ClientError as exc:
        logger.warning("Depot lookup failed: %s", exc)
        return set()
    return {r["depotId"] for r in rows if r.get("courier") is True and r.get("depotId")}


def handle_find_and_reserve_part(args: dict) -> dict:
    correlation_id = args.get("correlationId")

    err = validate_required(args, ["caseId", "partId", "mode", "idempotencyKey"], correlation_id)
    if err:
        return err

    if args["mode"] not in ("HOLD", "CONFIRM"):
        return error_envelope("VALIDATION_ERROR", "mode must be HOLD or CONFIRM", correlation_id)

    hit, cached = check_idempotency(args["idempotencyKey"], "findAndReservePart", args)
    if hit:
        return cached

    case_id = args["caseId"]
    part_id = args["partId"]

    # Optional approval (technician part request or an over-threshold part a manager approved).
    approval = None
    if args.get("approvalId"):
        try:
            approval = get_dynamodb().Table(TABLE_APPROVALS).get_item(
                Key={"approvalId": args["approvalId"]}).get("Item")
        except ClientError as exc:
            logger.error("Approval lookup failed: %s", exc)
            return error_envelope("DOWNSTREAM_TIMEOUT", "Approvals unavailable", correlation_id, retryable=True)
        if not approval:
            return error_envelope("NOT_FOUND", f"Approval {args['approvalId']} not found", correlation_id)
        if (approval.get("status") != "APPROVED" or approval.get("caseId") != case_id
                or not str(approval.get("action") or "").endswith(f":{part_id}")):
            return error_envelope(
                "POLICY_DENIED",
                f"Approval {args['approvalId']} is not an APPROVED approval for part {part_id} on case {case_id}",
                correlation_id,
                details={"approvalStatus": approval.get("status"), "action": approval.get("action")},
            )

    try:
        qty = int((approval or {}).get("qty") or 1)
    except (TypeError, ValueError):
        qty = 1
    qty = max(qty, 1)

    requested_by = (approval or {}).get("requestedBy")
    home_depot = None
    if requested_by:
        home_depot = get_technician(requested_by).get("homeDepotId") or DEFAULT_DEPOT_ID
    target_depot = args.get("depotId") or home_depot or DEFAULT_DEPOT_ID

    try:
        part = get_dynamodb().Table(TABLE_PARTS).get_item(Key={"pk": f"PARTS#{part_id}", "sk": "META"}).get("Item") or {}
        stock_rows = _inventory_rows(part_id)
    except ClientError as exc:
        logger.error("Parts / inventory lookup failed: %s", exc)
        return error_envelope("DOWNSTREAM_TIMEOUT", "Parts inventory unavailable", correlation_id, retryable=True)

    if not part and not stock_rows:
        return error_envelope(
            "PART_UNAVAILABLE", f"Part {part_id} not found in inventory", correlation_id,
            details={"partId": part_id, "checkedDepots": [target_depot]},
        )

    value_usd = float(part.get("unitValueUsd", part.get("priceUsd", 0.0)) or 0.0) * qty
    if value_usd > AUTO_APPROVE_THRESHOLD_USD and approval is None:
        return error_envelope(
            "NEEDS_APPROVAL",
            f"Part value ${value_usd:.2f} exceeds auto-approve threshold ${AUTO_APPROVE_THRESHOLD_USD:.2f}",
            correlation_id,
            details={"valueUsd": value_usd, "autoApproveThresholdUsd": AUTO_APPROVE_THRESHOLD_USD},
        )

    # Prefer the target depot; otherwise the depot with the most free stock (then transfer it over).
    free = {row["depotId"]: row["available"] for row in stock_rows}
    if free.get(target_depot, 0) >= qty:
        depot_id = target_depot
    else:
        courier_hubs = _courier_depot_ids()
        others = sorted(((d, n) for d, n in free.items() if d != target_depot and n >= qty),
                        key=lambda x: (x[0] in courier_hubs, -x[1], x[0]))
        if not others:
            return error_envelope(
                "PART_UNAVAILABLE", f"Part {part_id} x{qty} is out of stock", correlation_id,
                details={"partId": part_id, "qty": qty, "checkedDepots": sorted(free) or [target_depot]},
            )
        depot_id = others[0][0]

    reservation_id = new_id("res-")
    status = "HELD" if args["mode"] == "HOLD" else "CONFIRMED"
    get_dynamodb().Table(TABLE_RESERVATIONS).put_item(Item={
        "reservationId": reservation_id,
        "partId": part_id,
        "depotId": depot_id,
        "caseId": case_id,
        "qty": qty,
        "status": status,
        "valueUsd": str(value_usd),
        "idempotencyKey": args["idempotencyKey"],
        "createdAt": now_iso(),
        **({"approvalId": args["approvalId"]} if args.get("approvalId") else {}),
    })

    result = {
        "reservationId": reservation_id,
        "partId": part_id,
        "depotId": depot_id,
        "qty": qty,
        "status": status,
        "valueUsd": value_usd,
    }

    if depot_id != target_depot and (requested_by or args.get("depotId")):
        transfer = handle_transfer_part({
            "partId": part_id, "fromDepotId": depot_id, "toDepotId": target_depot, "quantity": qty,
            "idempotencyKey": f"{args['idempotencyKey']}:transfer", "correlationId": correlation_id,
        })
        result["transfer"] = transfer

    if not requested_by:
        # The agent's own pick is the part the technician is expected to use (mobile app: requiredParts).
        _set_required_part(case_id, part_id, part, qty)

    if requested_by:
        result["technicianNotification"] = notify_technician(
            "PART_APPROVED", requested_by, case_id, _work_order_for(case_id, requested_by),
            idempotency_key=args["idempotencyKey"],
            extra={"partId": part_id, "qty": qty, "depotId": target_depot,
                   "etaAt": (result.get("transfer") or {}).get("etaAt")},
        )

    save_idempotency(args["idempotencyKey"], "findAndReservePart", args, result)
    return result


def _set_required_part(case_id: str, part_id: str, part: dict, qty: int) -> None:
    """Set META.requiredPart (the part the technician should consume) unless the case already has one.
    Best effort: never fails the reservation."""
    spec = {"partId": part_id, "name": part.get("name") or part.get("description") or part_id, "qty": qty}
    if part.get("unitValueUsd") is not None:
        spec["unitValueUsd"] = part["unitValueUsd"]
    try:
        get_dynamodb().Table(TABLE_CASES).update_item(
            Key={"pk": f"CASE#{case_id}", "sk": "META"},
            UpdateExpression="SET requiredPart = :p",
            ConditionExpression="attribute_exists(pk) AND attribute_not_exists(requiredPart)",
            ExpressionAttributeValues={":p": spec},
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            logger.warning("Could not set requiredPart on %s: %s", case_id, exc)


# Used when the DiagnosticAgent left no work instructions on the case, so the technician is never blocked
# by an empty checklist (the mobile app refuses to complete a job without one).
DEFAULT_CHECKLIST = (
    "Make the equipment safe: isolate power and apply lock-out",
    "Inspect and repair the reported fault",
    "Replace the faulty part if required and record it in the app",
    "Run the equipment and confirm readings are back in the normal band",
)


def _job_field_work(case_id: str) -> dict:
    """checklist / manualRef / fieldBrief for the technician's APPT# item, from META.fieldWork (written by the
    orchestrator from the DiagnosticAgent's answer), else the generic default checklist."""
    work = {}
    try:
        meta = get_dynamodb().Table(TABLE_CASES).get_item(
            Key={"pk": f"CASE#{case_id}", "sk": "META"}, ProjectionExpression="fieldWork").get("Item") or {}
        work = meta.get("fieldWork") or {}
    except ClientError as exc:
        logger.warning("fieldWork lookup failed for %s: %s", case_id, exc)
    steps = [s for s in (work.get("checklist") or []) if isinstance(s, str) and s.strip()] or list(DEFAULT_CHECKLIST)
    out = {"checklist": [{"item": s, "done": False} for s in steps]}
    if work.get("manualRef"):
        out["manualRef"] = work["manualRef"]
    if work.get("brief"):
        out["fieldBrief"] = {"text": work["brief"], "confidence": None}
    return out


def _inventory_rows(part_id: str) -> list:
    """[{depotId, available}] for every depot that stocks the part (available = onHand - reserved)."""
    from boto3.dynamodb.conditions import Attr

    table = get_dynamodb().Table(TABLE_INVENTORY)
    kwargs = {"FilterExpression": Attr("sk").eq("META") & Attr("pk").begins_with("INV#")}
    rows = []
    while True:
        resp = table.scan(**kwargs)
        for item in resp.get("Items", []):
            parts = str(item.get("pk", "")).split("#")
            if len(parts) != 3 or parts[2] != part_id:
                continue
            available = int(item.get("onHand", 0) or 0) - int(item.get("reserved", 0) or 0)
            rows.append({"depotId": parts[1], "available": max(available, 0)})
        if not resp.get("LastEvaluatedKey"):
            return rows
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def _work_order_for(case_id: str, technician_id: str) -> str:
    """The technician's work order (APPT#<id>) on the case, or '-'."""
    from boto3.dynamodb.conditions import Key

    try:
        resp = get_dynamodb().Table(TABLE_CASES).query(
            KeyConditionExpression=Key("pk").eq(f"CASE#{case_id}") & Key("sk").begins_with("APPT#"))
    except ClientError as exc:
        logger.warning("Appointment lookup failed for %s: %s", case_id, exc)
        return "-"
    for item in resp.get("Items", []):
        if item.get("technicianId") == technician_id:
            return str(item["sk"]).removeprefix("APPT#")
    return "-"


def handle_release_part_reservation(args: dict) -> dict:
    correlation_id = args.get("correlationId")

    err = validate_required(args, ["reservationId", "idempotencyKey"], correlation_id)
    if err:
        return err

    hit, cached = check_idempotency(args["idempotencyKey"], "releasePartReservation", args)
    if hit:
        return cached

    reservation_id = args["reservationId"]
    table = get_dynamodb().Table(TABLE_RESERVATIONS)
    try:
        table.update_item(
            Key={"reservationId": reservation_id},
            UpdateExpression="SET #s = :s, releasedAt = :t",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "RELEASED", ":t": now_iso()},
        )
    except ClientError as exc:
        logger.warning("Reservation update failed: %s", exc)

    result = {"reservationId": reservation_id, "status": "RELEASED"}
    save_idempotency(args["idempotencyKey"], "releasePartReservation", args, result)
    return result


def handle_transfer_part(args: dict) -> dict:
    correlation_id = args.get("correlationId")

    err = validate_required(args, ["partId", "fromDepotId", "toDepotId", "quantity", "idempotencyKey"], correlation_id)
    if err:
        return err

    qty = args.get("quantity", 0)
    if not isinstance(qty, (int, float)) or qty <= 0:
        return error_envelope("VALIDATION_ERROR", "quantity must be a positive number", correlation_id)

    hit, cached = check_idempotency(args["idempotencyKey"], "transferPart", args)
    if hit:
        return cached

    transfer_id = new_id("xfr-")
    eta_at = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat().replace("+00:00", "Z")

    table = get_dynamodb().Table(TABLE_TRANSFERS)
    table.put_item(Item={
        "transferId": transfer_id,
        "partId": args["partId"],
        "fromDepotId": args["fromDepotId"],
        "toDepotId": args["toDepotId"],
        "quantity": qty,
        "status": "IN_TRANSIT",
        "etaAt": eta_at,
        "idempotencyKey": args["idempotencyKey"],
        "createdAt": now_iso(),
    })

    result = {"transferId": transfer_id, "status": "IN_TRANSIT", "etaAt": eta_at}
    save_idempotency(args["idempotencyKey"], "transferPart", args, result)
    return result

# ---------------------------------------------------------------------------
# sendCaseUpdateEmail — SES-backed customer email notifications
# ---------------------------------------------------------------------------

def _is_important_update(update_type: str) -> bool:
    """
    Returns True only if the updateType is in the configured allow-list.

    Examples of IMPORTANT (email sent):
      CASE_CLOSED, APPOINTMENT_SCHEDULED, PART_APPROVAL_REQUIRED, ESCALATED

    Examples of NOT important (email suppressed):
      NOTE_ADDED, INTERNAL_FLAG_SET, AGENT_ASSIGNED, LOG_ENTRY, FIELD_UPDATED
    """
    return update_type in IMPORTANT_UPDATE_TYPES


def _check_throttle(case_id: str, correlation_id: str = None) -> bool:
    """
    Returns True (allowed) if the case has sent fewer than EMAIL_THROTTLE_MAX
    emails in the last EMAIL_THROTTLE_WINDOW seconds.
    Uses the conversations/messages table to count recent emails for this case.
    """
    table = get_dynamodb().Table(TABLE_MESSAGES)
    window_start = (
        datetime.now(timezone.utc) - timedelta(seconds=EMAIL_THROTTLE_WINDOW)
    ).isoformat().replace("+00:00", "Z")

    try:
        # Scan for recent email messages on this case within the throttle window.
        # In production, replace with a GSI query on caseId+sentAt for efficiency.
        resp = table.scan(
            FilterExpression=(
                "caseId = :cid AND channel = :ch AND sentAt >= :w"
            ),
            ExpressionAttributeValues={
                ":cid": case_id,
                ":ch":  "EMAIL",
                ":w":   window_start,
            },
            Select="COUNT",
        )
        count = resp.get("Count", 0)
        logger.info("Throttle check for case %s: %d emails in window (max %d)", case_id, count, EMAIL_THROTTLE_MAX)
        return count < EMAIL_THROTTLE_MAX
    except ClientError as exc:
        # On error, allow sending (fail open) but log it
        logger.warning("Throttle check failed, allowing send: %s", exc)
        return True


def _build_email_body(case_id: str, update_type: str, subject: str,
                      summary: str, details: dict) -> tuple[str, str]:
    """
    Builds (html_body, text_body) for the email.
    Returns a structured HTML email and a plain-text fallback.
    """
    detail_rows_html = "".join(
        f"<tr><td style='padding:4px 12px 4px 0;color:#666;'><b>{k}</b></td>"
        f"<td style='padding:4px 0;'>{v}</td></tr>"
        for k, v in details.items()
    )
    detail_rows_text = "\n".join(f"  {k}: {v}" for k, v in details.items())

    html = f"""
    <html>
    <body style="font-family:Arial,sans-serif;color:#222;max-width:600px;margin:auto;padding:24px;">
      <div style="background:#0055a5;padding:16px 24px;border-radius:6px 6px 0 0;">
        <h2 style="color:#fff;margin:0;">OpsResolve Case Update</h2>
      </div>
      <div style="border:1px solid #ddd;border-top:none;padding:24px;border-radius:0 0 6px 6px;">
        <p style="font-size:16px;margin-top:0;">{summary}</p>
        <table style="width:100%;border-collapse:collapse;margin-bottom:16px;">
          <tr>
            <td style="padding:4px 12px 4px 0;color:#666;"><b>Case ID</b></td>
            <td style="padding:4px 0;">{case_id}</td>
          </tr>
          <tr>
            <td style="padding:4px 12px 4px 0;color:#666;"><b>Update Type</b></td>
            <td style="padding:4px 0;">{update_type}</td>
          </tr>
          {detail_rows_html}
        </table>
        <hr style="border:none;border-top:1px solid #eee;margin:16px 0;">
        <p style="font-size:12px;color:#999;margin:0;">
          This is an automated notification from OpsResolve. If you have questions,
          reply to this email or contact your service representative.
        </p>
      </div>
    </body>
    </html>
    """

    text = f"""OpsResolve Case Update
======================
{summary}

Case ID     : {case_id}
Update Type : {update_type}
{detail_rows_text}

---
This is an automated notification from OpsResolve.
"""
    return html.strip(), text.strip()


def handle_send_case_update_email(args: dict) -> dict:
    """
    Sends a customer email notification for significant case updates via AWS SES.

    Importance filtering  — only updateTypes in IMPORTANT_UPDATE_TYPES trigger
                            an email; minor/internal updates are silently skipped.
    Throttling            — max EMAIL_THROTTLE_MAX emails per case per hour to
                            prevent email fatigue.
    Idempotency           — same idempotencyKey always returns the same result
                            with no duplicate send.

    Input args:
        caseId          (required) — service case ID
        recipientEmail  (required) — customer email address
        updateType      (required) — milestone/status type (see IMPORTANT_UPDATE_TYPES)
        subject         (required) — email subject line
        summary         (required) — one-sentence plain English summary shown at top
        details         (optional) — dict of key/value pairs shown in the email body
        idempotencyKey  (required)
        correlationId   (optional)

    Returns:
        { messageId, status, channel, recipient, updateType, suppressed? }
    """
    correlation_id = args.get("correlationId")

    # ── 1. Validate required fields ──────────────────────────────────────────
    err = validate_required(
        args,
        ["caseId", "recipientEmail", "updateType", "subject", "summary", "idempotencyKey"],
        correlation_id,
    )
    if err:
        return err

    case_id       = args["caseId"]
    recipient     = args.get("recipientEmail") or SES_DEFAULT_RECIPIENT
    update_type   = args["updateType"]
    subject       = args["subject"]
    summary       = args["summary"]
    details       = args.get("details", {})

    # ── 2. Importance filter — skip minor/internal updates ───────────────────
    if not _is_important_update(update_type):
        logger.info(
            "Email suppressed for case %s — updateType '%s' is not in important list",
            case_id, update_type,
        )
        result = {
            "suppressed": True,
            "reason": f"updateType '{update_type}' is not configured as an important update",
            "updateType": update_type,
            "caseId": case_id,
        }
        # Still save idempotency so repeated calls for the same suppressed event
        # don't re-evaluate — they'll get the same suppressed response back.
        save_idempotency(args["idempotencyKey"], "sendCaseUpdateEmail", args, result)
        return result

    # ── 3. Idempotency check ─────────────────────────────────────────────────
    hit, cached = check_idempotency(args["idempotencyKey"], "sendCaseUpdateEmail", args)
    if hit:
        return cached

    # ── 4. Throttle check — prevent email fatigue ────────────────────────────
    if not _check_throttle(case_id, correlation_id):
        logger.warning(
            "Email throttled for case %s — exceeded %d emails per %ds window",
            case_id, EMAIL_THROTTLE_MAX, EMAIL_THROTTLE_WINDOW,
        )
        return error_envelope(
            "THROTTLED",
            f"Email throttled: case {case_id} has reached the limit of "
            f"{EMAIL_THROTTLE_MAX} emails per hour. Try again later.",
            correlation_id,
            details={"caseId": case_id, "throttleWindowSeconds": EMAIL_THROTTLE_WINDOW},
            retryable=True,
        )

    # ── 5. Build email content ───────────────────────────────────────────────
    html_body, text_body = _build_email_body(case_id, update_type, subject, summary, details)

    # ── 6. Send via SES ──────────────────────────────────────────────────────
    message_id = new_id("ses-")
    sent_at    = now_iso()

    try:
        ses_resp = get_ses().send_email(
            Source=SES_SENDER_EMAIL,
            Destination={"ToAddresses": [recipient]},
            ReplyToAddresses=[SES_REPLY_TO],
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Html": {"Data": html_body, "Charset": "UTF-8"},
                    "Text": {"Data": text_body, "Charset": "UTF-8"},
                },
            },
        )
        ses_message_id = ses_resp["MessageId"]
        logger.info(
            "SES email sent for case %s updateType %s → SES MessageId %s",
            case_id, update_type, ses_message_id,
        )
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        logger.error("SES send failed for case %s: %s", case_id, exc)

        if code in ("Throttling", "LimitExceeded"):
            return error_envelope("THROTTLED", f"SES throttled: {code}", correlation_id, retryable=True)
        if code == "MessageRejected":
            return error_envelope(
                "VALIDATION_ERROR",
                f"SES rejected the message — check sender/recipient addresses: {exc}",
                correlation_id,
            )
        return error_envelope(
            "DOWNSTREAM_TIMEOUT",
            f"SES send failed: {code}",
            correlation_id,
            retryable=True,
        )

    # ── 7. Log to conversations table ────────────────────────────────────────
    try:
        get_dynamodb().Table(TABLE_MESSAGES).put_item(Item={
            "pk":             message_id,
            "sk":             f"EMAIL#{case_id}",
            "messageId":      message_id,
            "sesMessageId":   ses_message_id,
            "caseId":         case_id,
            "channel":        "EMAIL",
            "updateType":     update_type,
            "recipient":      recipient,
            "subject":        subject,
            "status":         "SENT",
            "idempotencyKey": args["idempotencyKey"],
            "sentAt":         sent_at,
        })
    except ClientError as exc:
        # Non-fatal — email already sent, just log the failure
        logger.warning("Failed to log email record to DynamoDB: %s", exc)

    # ── 8. Build and cache result ────────────────────────────────────────────
    result = {
        "messageId":  message_id,
        "status":     "SENT",
        "channel":    "EMAIL",
        "recipient":  recipient,
        "updateType": update_type,
        "sentAt":     sent_at,
    }
    save_idempotency(args["idempotencyKey"], "sendCaseUpdateEmail", args, result)
    return result

# ---------------------------------------------------------------------------
# Technician availability (proposeAppointmentSlots / createServiceAppointment / reassign / cancel)
# ---------------------------------------------------------------------------

WINDOW_MINUTES = 120        # every availability window is 2 h
MAX_WINDOWS_PER_JOB = 2     # a longer job takes 2 back-to-back windows on the same day
MAX_SLOTS = 3
MAX_TECHNICIANS = 5


def _utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_utc(value):
    dt = _parse_iso(value) if value else None
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _window_bounds(item: dict):
    """(start, end) UTC datetimes of an availability row, or None if malformed."""
    try:
        day = str(item.get("date") or str(item.get("sk", "")).split("#")[1])
        start = datetime.fromisoformat(f"{day}T{item['start']}:00").replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(f"{day}T{item['end']}:00").replace(tzinfo=timezone.utc)
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    return (start, end) if start < end else None


def _technician_windows(technician_id: str, start_dt: datetime, end_dt: datetime, status: str = "AVAILABLE") -> list:
    """Availability rows for one technician whose window lies inside [start_dt, end_dt], sorted by start.
    Each: {pk, sk, slotId, start, end, appointmentId}. Duplicate (date, start) rows collapse to the first."""
    from boto3.dynamodb.conditions import Key

    table = get_dynamodb().Table(TABLE_FIELD_AVAILABILITY)
    kwargs = {"KeyConditionExpression": Key("pk").eq(f"TECHNICIAN#{technician_id}") & Key("sk").between(
        f"SLOT#{start_dt.date().isoformat()}", f"SLOT#{end_dt.date().isoformat()}~")}
    items = []
    while True:
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        if not resp.get("LastEvaluatedKey"):
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    out, seen = [], set()
    for item in sorted(items, key=lambda i: str(i.get("sk"))):
        if item.get("status") != status:
            continue
        bounds = _window_bounds(item)
        if not bounds or bounds[0] < start_dt or bounds[1] > end_dt or bounds in seen:
            continue
        seen.add(bounds)
        out.append({"pk": item["pk"], "sk": item["sk"], "slotId": item.get("slotId"),
                    "start": bounds[0], "end": bounds[1], "appointmentId": item.get("appointmentId")})
    out.sort(key=lambda w: w["start"])
    return out


def _windows_needed(duration_min: int) -> int:
    return 1 if duration_min <= WINDOW_MINUTES else 2


def _candidate_slots(windows: list, duration_min: int) -> list:
    """Bookable slots from sorted windows: single windows, or 2 back-to-back windows (end == next start;
    the 13:00-14:00 lunch gap is never bridged) for jobs longer than one window."""
    if _windows_needed(duration_min) == 1:
        return [[w] for w in windows]
    return [[a, b] for a, b in zip(windows, windows[1:]) if a["end"] == b["start"]]


def _rank_slots(candidates: list, restore_by: datetime, max_slots: int = MAX_SLOTS) -> list:
    """Verbatim rule of opsresolve.engines.slot_engine.propose_slots (no blocked hours): SLA-meeting first,
    then higher score, then earlier start. Kept inline: this Lambda ships without the opsresolve package."""
    pool = [dict(c, meetsSla=c["end"] <= restore_by) for c in candidates if c.get("partReady", True)]
    pool.sort(key=lambda c: (not c["meetsSla"], -c["score"], c["start"]))
    return pool[:max_slots]


def _case_restore_by(case_id: str):
    try:
        item = get_dynamodb().Table(TABLE_CASES).get_item(
            Key={"pk": f"CASE#{case_id}", "sk": "META"}, ProjectionExpression="restoreBy").get("Item") or {}
    except ClientError as exc:
        logger.warning("Case lookup failed for %s: %s", case_id, exc)
        return None
    return _as_utc(item.get("restoreBy"))


def _covering_windows(technician_id: str, slot_start: datetime, slot_end: datetime, status: str = "AVAILABLE"):
    """The technician's windows that exactly tile [slot_start, slot_end] (1 or 2 back-to-back), else None."""
    windows = _technician_windows(technician_id, slot_start, slot_end, status)
    if not windows or len(windows) > MAX_WINDOWS_PER_JOB:
        return None
    if windows[0]["start"] != slot_start or windows[-1]["end"] != slot_end:
        return None
    if any(a["end"] != b["start"] for a, b in zip(windows, windows[1:])):
        return None
    return windows


def _book_ops(windows: list, work_order_id: str, case_id: str) -> list:
    return [{"Update": {
        "TableName": TABLE_FIELD_AVAILABILITY,
        "Key": {"pk": w["pk"], "sk": w["sk"]},
        "UpdateExpression": "SET #s = :booked, appointmentId = :wo, caseId = :c, bookedAt = :t",
        "ConditionExpression": "#s = :avail",
        "ExpressionAttributeNames": {"#s": "status"},
        "ExpressionAttributeValues": {":booked": "BOOKED", ":avail": "AVAILABLE", ":wo": work_order_id,
                                      ":c": case_id, ":t": now_iso()},
    }} for w in windows]


def _free_ops(keys: list, work_order_id: str) -> list:
    return [{"Update": {
        "TableName": TABLE_FIELD_AVAILABILITY,
        "Key": {"pk": k["pk"], "sk": k["sk"]},
        "UpdateExpression": "SET #s = :avail, appointmentId = :none REMOVE caseId, bookedAt",
        "ConditionExpression": "#s = :booked AND appointmentId = :wo",
        "ExpressionAttributeNames": {"#s": "status"},
        "ExpressionAttributeValues": {":avail": "AVAILABLE", ":booked": "BOOKED", ":wo": work_order_id,
                                      ":none": None},
    }} for k in keys]


def _transact(ops: list) -> bool:
    """Run one all-or-nothing transaction. False when a condition failed (lost race / not available)."""
    if not ops:
        return True
    try:
        get_dynamodb().meta.client.transact_write_items(TransactItems=ops)
    except ClientError as exc:
        if exc.response["Error"].get("Code") in ("TransactionCanceledException", "ConditionalCheckFailedException"):
            return False
        raise
    return True


def _slot_keys(windows: list) -> list:
    return [{"pk": w["pk"], "sk": w["sk"]} for w in windows]


def handle_propose_appointment_slots(args: dict) -> dict:
    correlation_id = args.get("correlationId")

    err = validate_required(args, ["caseId", "technicianIds", "durationMin", "windowStart", "windowEnd"], correlation_id)
    if err:
        return err

    technician_ids = [str(t) for t in (args["technicianIds"] or []) if t]
    if not technician_ids:
        return error_envelope("NO_TECHNICIAN", "No technician IDs provided", correlation_id)

    try:
        duration_min = int(args["durationMin"])
    except (TypeError, ValueError):
        return error_envelope("VALIDATION_ERROR", "durationMin must be an integer", correlation_id)
    if duration_min <= 0 or duration_min > WINDOW_MINUTES * MAX_WINDOWS_PER_JOB:
        return error_envelope(
            "VALIDATION_ERROR",
            f"durationMin must be 1..{WINDOW_MINUTES * MAX_WINDOWS_PER_JOB} (at most 2 back-to-back 2 h windows)",
            correlation_id)

    window_start, window_end = _as_utc(args["windowStart"]), _as_utc(args["windowEnd"])
    if window_start is None or window_end is None:
        return error_envelope("VALIDATION_ERROR", "windowStart/windowEnd must be ISO-8601", correlation_id)
    if window_end <= window_start:
        return error_envelope("VALIDATION_ERROR", "windowEnd must be after windowStart", correlation_id)
    window_start = max(window_start, datetime.now(timezone.utc))  # only future windows
    if window_end <= window_start:
        return error_envelope("NO_TECHNICIAN", "The search window is in the past", correlation_id)

    restore_by = _case_restore_by(args["caseId"])
    sla_limit = restore_by or datetime.max.replace(tzinfo=timezone.utc)  # no SLA on record: every slot meets it

    ranked_ids = technician_ids[:MAX_TECHNICIANS]
    candidates = []
    for rank, tech_id in enumerate(ranked_ids):
        try:
            windows = _technician_windows(tech_id, window_start, window_end)
        except ClientError as exc:
            logger.error("Availability lookup failed for %s: %s", tech_id, exc)
            return error_envelope("DOWNSTREAM_TIMEOUT", "Technician availability unavailable", correlation_id,
                                  retryable=True)
        for group in _candidate_slots(windows, duration_min):
            candidates.append({
                "slotId": group[0]["slotId"], "technicianId": tech_id,
                "start": group[0]["start"], "end": group[-1]["end"],
                "score": len(ranked_ids) - rank,  # first technician id = best ranked
                "availabilitySlotIds": [w["slotId"] for w in group],
            })

    best = _rank_slots(candidates, sla_limit)
    if not best:
        return error_envelope(
            "NO_TECHNICIAN", "No technician has free availability windows in the requested range", correlation_id,
            details={"technicianIds": ranked_ids, "windowStart": _utc_iso(window_start),
                     "windowEnd": _utc_iso(window_end), "durationMin": duration_min},
        )
    return {
        "slots": [{
            "technicianId": c["technicianId"],
            "slotStart": _utc_iso(c["start"]),
            "slotEnd": _utc_iso(c["end"]),
            "meetsSla": c["meetsSla"],
            "availabilitySlotIds": c["availabilitySlotIds"],
        } for c in best],
        "restoreBy": _utc_iso(restore_by) if restore_by else None,
        "durationMin": duration_min,
    }


def handle_create_service_appointment(args: dict) -> dict:
    correlation_id = args.get("correlationId")

    # consentRef is a governance requirement — validate server-side regardless of caller
    err = validate_required(
        args,
        ["caseId", "technicianId", "slotStart", "slotEnd", "consentRef", "idempotencyKey"],
        correlation_id,
    )
    if err:
        return err

    if not args.get("consentRef", "").strip():
        return error_envelope(
            "VALIDATION_ERROR",
            "consentRef is mandatory (Section 10.2 governance requirement)",
            correlation_id,
        )

    slot_start, slot_end = _as_utc(args["slotStart"]), _as_utc(args["slotEnd"])
    if slot_start is None or slot_end is None or slot_end <= slot_start:
        return error_envelope("VALIDATION_ERROR", "slotStart/slotEnd must be ISO-8601 with start < end", correlation_id)

    hit, cached = check_idempotency(args["idempotencyKey"], "createServiceAppointment", args)
    if hit:
        return cached

    case_id, technician_id = args["caseId"], args["technicianId"]
    work_order_id = new_id("wo-")

    # Book the technician's availability window(s) first: all-or-nothing, conditional on AVAILABLE.
    windows = _covering_windows(technician_id, slot_start, slot_end)
    if not windows or not _transact(_book_ops(windows, work_order_id, case_id)):
        return error_envelope(
            "CONFLICT_STATE",
            f"Technician {technician_id} is not available for {fmt_slot(args['slotStart'], args['slotEnd'])}",
            correlation_id,
            details={"technicianId": technician_id, "slotStart": args["slotStart"], "slotEnd": args["slotEnd"]},
        )
    slot_keys = _slot_keys(windows)
    start_iso, end_iso = _utc_iso(slot_start), _utc_iso(slot_end)
    created_at = now_iso()

    appt_table = get_dynamodb().Table(TABLE_APPOINTMENTS)
    appt_table.put_item(Item={
        "workOrderId": work_order_id,
        "caseId": case_id,
        "technicianId": technician_id,
        "slotStart": start_iso,
        "slotEnd": end_iso,
        "consentRef": args["consentRef"],
        "status": "SCHEDULED",
        "availabilitySlots": slot_keys,
        "idempotencyKey": args["idempotencyKey"],
        "createdAt": created_at,
    })
    # The mobile app (and GET /cases/{id} appointments[]) read jobs from APPT#<workOrderId> in the cases table.
    get_dynamodb().Table(TABLE_CASES).put_item(Item={
        "pk": f"CASE#{case_id}",
        "sk": f"APPT#{work_order_id}",
        "caseId": case_id,
        "workOrderId": work_order_id,
        "technicianId": technician_id,
        "slotStart": start_iso,
        "slotEnd": end_iso,
        "status": "SCHEDULED",
        "consentRef": args["consentRef"],
        "createdAt": created_at,
        **_job_field_work(case_id),
    })

    result = {
        "workOrderId": work_order_id,
        "technicianId": technician_id,
        "slotStart": start_iso,
        "slotEnd": end_iso,
        "status": "SCHEDULED",
        "availabilitySlotIds": [w["slotId"] for w in windows],
    }
    result["technicianNotification"] = notify_technician(
        "ASSIGNED", args["technicianId"], args["caseId"], work_order_id,
        args["slotStart"], args["slotEnd"], args["idempotencyKey"],
    )
    save_idempotency(args["idempotencyKey"], "createServiceAppointment", args, result)
    return result


def handle_reassign_technician(args: dict) -> dict:
    correlation_id = args.get("correlationId")

    err = validate_required(args, ["workOrderId", "newTechnicianId", "reason", "idempotencyKey"], correlation_id)
    if err:
        return err

    hit, cached = check_idempotency(args["idempotencyKey"], "reassignTechnician", args)
    if hit:
        return cached

    work_order_id = args["workOrderId"]
    new_tech_id = args["newTechnicianId"]

    # The appointment (written by createServiceAppointment) knows the case, the slot and the previous technician.
    appt_table = get_dynamodb().Table(TABLE_APPOINTMENTS)
    appt = {}
    try:
        appt = appt_table.get_item(Key={"workOrderId": work_order_id}).get("Item") or {}
    except ClientError as exc:
        logger.warning("Appointment lookup failed for %s: %s", work_order_id, exc)
    old_tech_id = appt.get("technicianId")

    # Availability-backed booking: move it (book the new technician's same windows, free the old ones) in one
    # transaction before anything else changes, so a busy technician leaves the booking untouched.
    new_slot_keys = None
    if appt.get("availabilitySlots") and old_tech_id != new_tech_id:
        new_windows = _covering_windows(new_tech_id, _as_utc(appt.get("slotStart")), _as_utc(appt.get("slotEnd")))
        ops = None
        if new_windows:
            ops = (_book_ops(new_windows, work_order_id, appt.get("caseId") or "-")
                   + _free_ops(appt["availabilitySlots"], work_order_id))
        if not ops or not _transact(ops):
            return error_envelope(
                "CONFLICT_STATE",
                f"Technician {new_tech_id} is not available for {fmt_slot(appt.get('slotStart'), appt.get('slotEnd'))}",
                correlation_id,
                details={"technicianId": new_tech_id, "workOrderId": work_order_id},
            )
        new_slot_keys = _slot_keys(new_windows)

    wo_table = get_dynamodb().Table(TABLE_WORK_ORDERS)
    try:
        wo_table.update_item(
            Key={"pk": f"WORK_ORDER_HISTORY#{work_order_id}", "sk": "META"},
            UpdateExpression="SET technicianId = :t, #s = :s, reassignReason = :r, updatedAt = :u",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":t": new_tech_id,
                ":s": "REASSIGNED",
                ":r": args["reason"],
                ":u": now_iso(),
            },
        )
    except ClientError as exc:
        logger.warning("Work order reassign failed: %s", exc)

    result = {"workOrderId": work_order_id, "technicianId": new_tech_id, "status": "REASSIGNED"}

    if appt:
        try:
            update = "SET technicianId = :t, previousTechnicianId = :p, updatedAt = :u"
            values = {":t": new_tech_id, ":p": old_tech_id or "-", ":u": now_iso()}
            appt_values = dict(values)
            if new_slot_keys is not None:
                update_appt = update + ", availabilitySlots = :a"
                appt_values[":a"] = new_slot_keys
            else:
                update_appt = update
            appt_table.update_item(Key={"workOrderId": work_order_id}, UpdateExpression=update_appt,
                                   ExpressionAttributeValues=appt_values)
            if appt.get("caseId"):
                # Mobile job moves to the new technician.
                get_dynamodb().Table(TABLE_CASES).update_item(
                    Key={"pk": f"CASE#{appt['caseId']}", "sk": f"APPT#{work_order_id}"},
                    UpdateExpression=update,
                    ConditionExpression="attribute_exists(pk)",
                    ExpressionAttributeValues=values,
                )
        except ClientError as exc:
            logger.warning("Appointment update failed for %s: %s", work_order_id, exc)

    if appt.get("caseId"):
        notifications = []
        old_tech_id = appt.get("technicianId")
        if old_tech_id and old_tech_id != new_tech_id:
            notifications.append(notify_technician(
                "RELEASED", old_tech_id, appt["caseId"], work_order_id, idempotency_key=args["idempotencyKey"]))
        notifications.append(notify_technician(
            "ASSIGNED", new_tech_id, appt["caseId"], work_order_id,
            appt.get("slotStart"), appt.get("slotEnd"), args["idempotencyKey"]))
        result["technicianNotifications"] = notifications
    else:
        logger.info("No appointment for %s: technicians not notified", work_order_id)

    save_idempotency(args["idempotencyKey"], "reassignTechnician", args, result)
    return result


def handle_update_work_order(args: dict) -> dict:
    correlation_id = args.get("correlationId")

    err = validate_required(args, ["workOrderId", "idempotencyKey"], correlation_id)
    if err:
        return err

    hit, cached = check_idempotency(args["idempotencyKey"], "updateWorkOrder", args)
    if hit:
        return cached

    work_order_id = args["workOrderId"]
    updated_at = now_iso()
    new_status = args.get("status", "UPDATED")

    update_expr_parts = ["#s = :s", "updatedAt = :u"]
    expr_names = {"#s": "status"}
    expr_values = {":s": new_status, ":u": updated_at}

    if args.get("notes"):
        update_expr_parts.append("notes = :n")
        expr_values[":n"] = args["notes"]

    if args.get("partsUsed"):
        update_expr_parts.append("partsUsed = :p")
        expr_values[":p"] = args["partsUsed"]

    wo_table = get_dynamodb().Table(TABLE_WORK_ORDERS)
    try:
        wo_table.update_item(
            Key={"pk": f"WORK_ORDER_HISTORY#{work_order_id}", "sk": "META"},
            UpdateExpression="SET " + ", ".join(update_expr_parts),
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
        )
    except ClientError as exc:
        logger.warning("Work order update failed: %s", exc)

    result = {"workOrderId": work_order_id, "status": new_status, "updatedAt": updated_at}

    if new_status == "CANCELLED":
        result["availabilityFreed"] = _cancel_appointment(work_order_id, updated_at)

    save_idempotency(args["idempotencyKey"], "updateWorkOrder", args, result)
    return result


def _cancel_appointment(work_order_id: str, updated_at: str) -> int:
    """Cancel an availability-backed appointment: free its windows, mark the appointment row CANCELLED.
    Returns the number of windows freed (0 for legacy appointments without availabilitySlots)."""
    appt_table = get_dynamodb().Table(TABLE_APPOINTMENTS)
    try:
        appt = appt_table.get_item(Key={"workOrderId": work_order_id}).get("Item") or {}
    except ClientError as exc:
        logger.warning("Appointment lookup failed for %s: %s", work_order_id, exc)
        return 0
    if not appt:
        return 0
    freed = 0
    for key in appt.get("availabilitySlots") or []:
        # One window at a time: a window the technician already freed or deleted must not block the rest.
        try:
            if _transact(_free_ops([key], work_order_id)):
                freed += 1
        except ClientError as exc:
            logger.warning("Freeing window %s failed for %s: %s", key.get("sk"), work_order_id, exc)
    try:
        appt_table.update_item(
            Key={"workOrderId": work_order_id},
            UpdateExpression="SET #s = :s, updatedAt = :u",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "CANCELLED", ":u": updated_at},
        )
    except ClientError as exc:
        logger.warning("Appointment cancel failed for %s: %s", work_order_id, exc)
    if appt.get("caseId"):  # the mobile job and GET /cases/{id} read the APPT# row in the cases table
        try:
            get_dynamodb().Table(TABLE_CASES).update_item(
                Key={"pk": f"CASE#{appt['caseId']}", "sk": f"APPT#{work_order_id}"},
                UpdateExpression="SET #s = :s, updatedAt = :u",
                ConditionExpression="attribute_exists(pk)",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":s": "CANCELLED", ":u": updated_at},
            )
        except ClientError as exc:
            logger.warning("Job cancel failed for %s: %s", work_order_id, exc)
    return freed


# ---------------------------------------------------------------------------
# Channel selection logic (sendCustomerMessage)
# ---------------------------------------------------------------------------

ORDER = ["RCS", "WHATSAPP", "SMS", "EMAIL"]

CAPABLE = {
    "APPOINTMENT_OPTIONS": {"RCS", "WHATSAPP", "SMS"},
    "RECOVERY_OFFER":      {"RCS", "WHATSAPP", "SMS"},
    "REPORT":              {"EMAIL", "RCS"},
    "CSAT":                {"RCS", "WHATSAPP", "SMS"},
    "ETA_UPDATE":          {"RCS", "WHATSAPP", "SMS"},
    "CONFIRMATION":        {"RCS", "WHATSAPP", "SMS", "EMAIL"},
    "CLARIFY":             {"WHATSAPP", "RCS", "SMS"},
    "URGENT_ALERT":        {"RCS", "SMS", "WHATSAPP"},
    "APPROVAL_REQUEST":    {"SMS"},
}


def select_channel(intent: str, consent: dict, preferred: str, active_channel: str = None,
                   wa_window_open: bool = False, priority: str = "P3", unhealthy: set = None) -> list:
    unhealthy = unhealthy or set()
    # SMS has no origination identity (project decision D11): never selected, WhatsApp / email instead.
    ok = {c for c, v in consent.items() if v == "OPTED_IN" and c not in unhealthy and c != "SMS"}
    capable = CAPABLE.get(intent, set(ORDER))

    if intent == "APPROVAL_REQUEST":
        return [c for c in ("WHATSAPP", "EMAIL") if c in ok][:1]
    if intent == "REPORT":
        for c in ["EMAIL", "RCS"]:
            if c in ok:
                return [c]
        return []
    if intent == "URGENT_ALERT" and priority == "P1":
        return [c for c in ["RCS", "WHATSAPP"] if c in ok]
    if active_channel == "WHATSAPP" and wa_window_open and "WHATSAPP" in ok and "WHATSAPP" in capable:
        return ["WHATSAPP"]
    if preferred in ok and preferred in capable:
        return [preferred]
    for c in ORDER:
        if c in ok and c in capable:
            return [c]
    return []


# ---------------------------------------------------------------------------
# RCS: signed postbacks, rich message builders, send
# ---------------------------------------------------------------------------
# Postback format (must stay identical to src/opsresolve/inbound/postback.py, which the inbound
# normalizer uses to verify taps):
#   opsr1|<caseId>|<intent>|<optionId>|<expiresEpoch>|<nonce>|<hmac32>
# hmac32 = first 32 hex chars of HMAC-SHA256(key, "opsr1|<caseId>|<intent>|<optionId>|<expiresEpoch>|<nonce>").
# The key is the secret's SecretString used as-is (UTF-8 bytes).

POSTBACK_VERSION = "opsr1"
POSTBACK_MAX_LEN = 2048
RCS_LABEL_MAX = 25
RCS_TITLE_MAX = 200
RCS_DESC_MAX = 2000
RCS_BODY_MAX = 3072

# How long a button stays valid (seconds). Offers expire sooner than plain replies.
POSTBACK_TTL_S = {"APPOINTMENT_OPTIONS": 3600, "RECOVERY_OFFER": 1800}
POSTBACK_TTL_DEFAULT_S = 86400
# TimeToLive on the RCS message itself (carrier drops it if undelivered by then).
MESSAGE_TTL_S = {"APPOINTMENT_OPTIONS": 3600, "RECOVERY_OFFER": 1800}

DEFAULT_TEXT = {
    "APPOINTMENT_OPTIONS": "Please choose a visit time that suits you.",
    "CONFIRMATION":        "Your service request has been updated.",
    "RECOVERY_OFFER":      "We have an option to get you back on track sooner.",
    "ETA_UPDATE":          "Here is an update on your engineer's arrival.",
    "CLARIFY":             "We need a little more information to continue.",
    "REPORT":              "Your service report is ready.",
    "CSAT":                "How did we do? Please rate your service experience.",
    "URGENT_ALERT":        "Your equipment needs attention.",
}


def _postback_key() -> bytes:
    global _postback_key_cache
    if _postback_key_cache is None:
        resp = get_secrets_client().get_secret_value(SecretId=POSTBACK_KEY_SECRET_ID)
        _postback_key_cache = resp["SecretString"].encode()
    return _postback_key_cache


def _pb_part(value, default: str = "-") -> str:
    text = default if value in (None, "") else str(value)
    return text.replace("|", "-")  # '|' is the field separator


def sign_postback(case_id: str, intent: str, option_id: str = "-", ttl_s: int = POSTBACK_TTL_DEFAULT_S) -> str:
    payload = "|".join([
        POSTBACK_VERSION, _pb_part(case_id), _pb_part(intent), _pb_part(option_id),
        str(int(time.time()) + ttl_s), pysecrets.token_hex(4),
    ])
    mac = hmac.new(_postback_key(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    value = f"{payload}|{mac}"
    if len(value) > POSTBACK_MAX_LEN:
        raise ValueError("postback exceeds 2048 characters")
    return value


def _trim(text, limit: int):
    if text is None:
        return None
    text = str(text).strip()
    return text[:limit] if text else None


def _pick(source: dict, *keys):
    """First non-empty string/number value among keys."""
    for key in keys:
        value = source.get(key)
        if value not in (None, "") and not isinstance(value, (dict, list)):
            return str(value)
    return None


def _parse_iso(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _reply(case_id: str, label: str, intent: str, option_id: str, ttl_s: int) -> dict:
    return {"Reply": {
        "Text": _trim(label, RCS_LABEL_MAX) or "OK",
        "PostbackData": sign_postback(case_id, intent, option_id, ttl_s),
    }}


def _options(data: dict) -> list:
    """Normalise data.slots / data.options into [{title, description, button, id, intent}].

    Slot objects from proposeAppointmentSlots ({technicianId, slotStart, slotEnd}) are accepted as-is.
    Without an explicit slotId/optionId/id, a slot's id is "<technicianId>~<slotStart>~<slotEnd>" so the
    tap carries everything needed to book it.
    """
    raw = data.get("slots") or data.get("options") or []
    out = []
    for idx, item in enumerate(raw):
        if isinstance(item, str):
            item = {"label": item}
        if not isinstance(item, dict):
            continue
        title = _pick(item, "title", "label", "text", "name")
        description = _pick(item, "description", "detail", "body")
        start = _parse_iso(item.get("slotStart"))
        if start and not title:
            title = fmt_slot(item.get("slotStart"), item.get("slotEnd"))
            description = description or (f"Technician {item['technicianId']}" if item.get("technicianId") else None)
        if not title:
            continue
        option_id = _pick(item, "optionId", "slotId", "id")
        if not option_id and item.get("slotStart"):
            option_id = f"{item.get('technicianId') or ''}~{item['slotStart']}~{item.get('slotEnd') or ''}"
        local_start = _local_dt(item.get("slotStart")) if start else None
        button = _pick(item, "button", "buttonLabel") or (f"Book {local_start.strftime('%H:%M')} UTC" if local_start else title)
        out.append({
            "title": title,
            "description": description,
            "button": button,
            "id": option_id or str(idx + 1),
            "intent": _pick(item, "intent", "action"),
            "slotStart": item.get("slotStart"),   # raw slot times, so WhatsApp can show them in local time
            "slotEnd": item.get("slotEnd"),
            "technicianId": item.get("technicianId"),
        })
    return out


def _default_option_intent(intent: str, index: int) -> str:
    if intent == "APPOINTMENT_OPTIONS":
        return "BOOK_SLOT"
    if intent == "RECOVERY_OFFER":
        return "RECOVERY_ACCEPT" if index == 0 else "RECOVERY_DECLINE"
    if intent == "CLARIFY":
        return "CLARIFY_REPLY"
    return "REPLY"


def build_rcs_content(intent: str, case_id: str, data: dict):
    """Build (RcsMessageContent, TimeToLive or None, plain-text summary) for SendRcsMessage."""
    data = data if isinstance(data, dict) else {}
    pb_ttl = POSTBACK_TTL_S.get(intent, POSTBACK_TTL_DEFAULT_S)
    title = _pick(data, "title", "heading", "subject")
    body = (_pick(data, "body", "text", "message", "description", "summary")
            or DEFAULT_TEXT.get(intent)
            or (f"Template {data['template']}" if data.get("template") else "Update on your service request."))
    options = _options(data)
    human = lambda: _reply(case_id, "Talk to a person", "HUMAN", "-", pb_ttl)  # noqa: E731
    ttl = MESSAGE_TTL_S.get(intent)
    summary = " - ".join(p for p in (title, body) if p)

    # CSAT: plain text + 1-5 rating buttons.
    if intent == "CSAT":
        ratings = [(5, "5 - great"), (4, "4"), (3, "3"), (2, "2"), (1, "1 - poor")]
        content = {
            "Content": {"TextMessage": {"Body": _trim(body, RCS_BODY_MAX)}},
            "Suggestions": [_reply(case_id, label, "CSAT", str(score), pb_ttl) for score, label in ratings],
        }
        return content, ttl, summary

    # Appointment slots: carousel of up to 3 cards (2 minimum), one "Book" button per card.
    if intent == "APPOINTMENT_OPTIONS" and len(options) >= 2:
        cards = []
        for opt in options[:3]:
            card = {
                "Title": _trim(opt["title"], RCS_TITLE_MAX),
                "Suggestions": [_reply(case_id, opt["button"], opt["intent"] or "BOOK_SLOT", opt["id"], pb_ttl)],
            }
            if opt["description"]:
                card["Description"] = _trim(opt["description"], RCS_DESC_MAX)
            cards.append(card)
        content = {
            "Content": {"Carousel": {"CardWidth": "MEDIUM", "CardContents": cards}},
            "Suggestions": [_reply(case_id, "None suit me", "NO_SLOT_FITS", "-", pb_ttl), human()],
        }
        summary = " | ".join(o["title"] for o in options[:3]) or summary
        return content, ttl, summary

    # Everything else: a rich card (title + body + up to 4 buttons), or plain text when there is nothing to tap.
    suggestions = [
        _reply(case_id, opt["button"], opt["intent"] or _default_option_intent(intent, i), opt["id"], pb_ttl)
        for i, opt in enumerate(options[:3])
    ]
    if intent == "URGENT_ALERT" and not suggestions:
        suggestions.append(_reply(case_id, "See options", "SHOW_OPTIONS", "-", pb_ttl))

    if intent == "CONFIRMATION":
        cal = data.get("calendar") if isinstance(data.get("calendar"), dict) else None
        start = _parse_iso(cal.get("start") or cal.get("startTime")) if cal else None
        end = _parse_iso(cal.get("end") or cal.get("endTime")) if cal else None
        if start and end:
            event = {
                "Text": "Add to calendar",
                "PostbackData": sign_postback(case_id, "CAL", "-", pb_ttl),
                "Title": _trim(cal.get("title") or title or "Service visit", 100),
                "StartTime": start,
                "EndTime": end,
            }
            if cal.get("description"):
                event["Description"] = _trim(cal["description"], 500)
            suggestions.append({"CreateCalendarEvent": event})
        loc = data.get("location") if isinstance(data.get("location"), dict) else None
        if loc and loc.get("latitude") is not None and loc.get("longitude") is not None:
            show = {
                "Text": "Site location",
                "PostbackData": sign_postback(case_id, "LOC", "-", pb_ttl),
                "Latitude": float(loc["latitude"]),
                "Longitude": float(loc["longitude"]),
            }
            if loc.get("label"):
                show["Label"] = _trim(loc["label"], 100)
            suggestions.append({"ShowLocation": show})
        if data.get("allowReschedule"):
            suggestions.append(_reply(case_id, "Reschedule", "RESCHEDULE", "-", pb_ttl))

    if intent in ("URGENT_ALERT", "RECOVERY_OFFER", "CLARIFY", "APPOINTMENT_OPTIONS") and len(suggestions) < 4:
        suggestions.append(human())

    desk_phone = _pick(data, "deskPhone", "servicePhone")
    if desk_phone and len(suggestions) < 4:
        suggestions.append({"DialPhone": {
            "Text": "Call service desk",
            "PostbackData": sign_postback(case_id, "CALL_DESK", "-", pb_ttl),
            "PhoneNumber": desk_phone,
        }})

    if not suggestions and not title:
        return {"Content": {"TextMessage": {"Body": _trim(body, RCS_BODY_MAX)}}}, ttl, summary

    card_content = {"Suggestions": suggestions[:4]}
    if title:
        card_content["Title"] = _trim(title, RCS_TITLE_MAX)
    card_content["Description"] = _trim(body, RCS_DESC_MAX)
    media_url = _pick(data, "mediaUrl", "imageUrl")
    if media_url and media_url.startswith(("https://", "s3://")):
        card_content["Media"] = {"FileUrl": media_url, "Height": "MEDIUM"}
    content = {"Content": {"RichCard": {"CardOrientation": "VERTICAL", "CardContent": card_content}}}
    return content, ttl, summary


def send_rcs(phone: str, content: dict, ttl, case_id: str, intent: str) -> str:
    """Call SendRcsMessage through the RCS agent. Returns the provider MessageId. No SMS fallback."""
    params = {
        "DestinationPhoneNumber": phone,
        "OriginationIdentity": RCS_AGENT_ID,
        "RcsMessageContent": content,
        "Context": {"caseId": case_id, "intent": intent},
    }
    if ttl:
        params["TimeToLive"] = ttl
    return get_sms_voice().send_rcs_message(**params)["MessageId"]


def get_contact(contact_id: str) -> dict:
    """mock-contacts is keyed pk="CONTACTS#<id>", sk="META". Accepts "CON-77" or "CONTACTS#CON-77"."""
    pk = contact_id if contact_id.startswith("CONTACTS#") else f"CONTACTS#{contact_id}"
    try:
        return get_dynamodb().Table(TABLE_CONTACTS).get_item(Key={"pk": pk, "sk": "META"}).get("Item", {})
    except ClientError as exc:
        logger.error("Contact lookup failed: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# WhatsApp: allowlist, 24h window, message builders, templates, send
# ---------------------------------------------------------------------------
# Meta limits (Cloud API): reply-button title 20, list row title 24 / description 72 / id 200,
# list button label 20, interactive body 1024, text body 4096, max 3 reply buttons, max 10 list rows.

WA_WINDOW_S = 24 * 3600
WA_CACHE_S = 300
WA_BUTTON_MAX = 20
WA_ROW_TITLE_MAX = 24
WA_ROW_DESC_MAX = 72
WA_ROW_ID_MAX = 200
WA_BODY_MAX = 1024
WA_TEXT_MAX = 4096
WA_MAX_BUTTONS = 3
WA_MAX_ROWS = 10
# All times are shown in UTC (project decision 2026-10-13: UTC everywhere, all channels).
DISPLAY_TZ = timezone.utc
DISPLAY_TZ_LABEL = "UTC"

_wa_allow_cache = {"at": 0.0, "numbers": frozenset()}
_wa_tpl_cache = {"at": 0.0, "approved": frozenset()}


def _digits(phone) -> str:
    return re.sub(r"\D", "", str(phone or ""))


def mask_phone(phone) -> str:
    """Log-safe phone: only the last 2 digits survive."""
    d = _digits(phone)
    return ("*" * max(len(d) - 2, 0) + d[-2:]) if d else "<none>"


def wa_user_key(phone) -> str:
    """Conversations-table key for a WhatsApp user. A hash, so the number itself is never stored there."""
    return "WAUSER#" + hashlib.sha256(_digits(phone).encode()).hexdigest()[:24]


def wa_allowlist() -> frozenset:
    """Digits-only numbers allowed to receive real WhatsApp messages (Secrets Manager, cached 5 min)."""
    now = time.time()
    if now - _wa_allow_cache["at"] < WA_CACHE_S:
        return _wa_allow_cache["numbers"]
    numbers, cached_for = frozenset(), WA_CACHE_S
    try:
        parsed = json.loads(get_secrets_client().get_secret_value(SecretId=WA_ALLOWLIST_SECRET_ID)["SecretString"])
        values = parsed.get("numbers", []) if isinstance(parsed, dict) else parsed
        numbers = frozenset(_digits(v) for v in values if _digits(v))
    except ClientError as exc:
        cached_for = 60  # retry soon: the secret may be created after the first call
        logger.warning("WhatsApp allowlist unavailable (%s): WhatsApp sends are DynamoDB-only",
                       exc.response["Error"].get("Code"))
    except (ValueError, AttributeError, TypeError):
        cached_for = 60
        logger.warning("WhatsApp allowlist secret is not valid JSON: WhatsApp sends are DynamoDB-only")
    _wa_allow_cache.update(at=now - WA_CACHE_S + cached_for, numbers=numbers)
    return numbers


def wa_window_is_open(phone) -> bool:
    """True when this number messaged us in the last 24h (written by the inbound normalizer)."""
    if not _digits(phone):
        return False
    try:
        item = get_dynamodb().Table(TABLE_MESSAGES).get_item(
            Key={"pk": wa_user_key(phone), "sk": "WINDOW"}).get("Item")
    except ClientError as exc:
        logger.warning("WhatsApp window lookup failed: %s", exc)
        return False
    last = _parse_iso(item.get("lastInboundAt")) if item else None
    if not last:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last).total_seconds() < WA_WINDOW_S


def wa_approved_templates() -> frozenset:
    """{(templateName, language)} with status APPROVED on the WABA (cached 5 min)."""
    now = time.time()
    if now - _wa_tpl_cache["at"] < WA_CACHE_S:
        return _wa_tpl_cache["approved"]
    approved = set()
    try:
        kwargs = {"id": WA_WABA_ID}
        while True:
            resp = get_social().list_whatsapp_message_templates(**kwargs)
            for tpl in resp.get("templates", []):
                if str(tpl.get("templateStatus", "")).upper() == "APPROVED":
                    approved.add((tpl.get("templateName"), tpl.get("templateLanguage")))
            if not resp.get("nextToken"):
                break
            kwargs["nextToken"] = resp["nextToken"]
    except ClientError as exc:
        logger.warning("WhatsApp template list failed: %s", exc.response["Error"].get("Code"))
    _wa_tpl_cache.update(at=now, approved=frozenset(approved))
    return _wa_tpl_cache["approved"]


def _local_dt(value):
    dt = _parse_iso(value) if value else None
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(DISPLAY_TZ)


def fmt_slot(start, end=None, label: bool = True) -> str:
    """'Mon 12 Oct 14:00-16:00 UTC' (or the raw value when it isn't an ISO time)."""
    s = _local_dt(start)
    if not s:
        return str(start or "-")
    e = _local_dt(end)
    text = s.strftime("%a %d %b %H:%M") + (f"-{e.strftime('%H:%M')}" if e else "")
    return f"{text} {DISPLAY_TZ_LABEL}" if label else text


def _one_line(value, limit: int = 300) -> str:
    """Template parameters may not contain newlines, tabs or 4+ consecutive spaces."""
    return (re.sub(r"\s+", " ", str(value or "")).strip() or "-")[:limit]


def _wa_button(case_id: str, label: str, intent: str, option_id: str, ttl_s: int) -> dict:
    return {"type": "reply", "reply": {
        "id": sign_postback(case_id, intent, option_id, ttl_s),
        "title": _trim(label, WA_BUTTON_MAX) or "OK",
    }}


def _wa_row(case_id: str, title: str, description, intent: str, option_id: str, ttl_s: int, index: int) -> dict:
    row_id = sign_postback(case_id, intent, option_id, ttl_s)
    if len(row_id) > WA_ROW_ID_MAX:  # very long slot ids: fall back to the option's position
        row_id = sign_postback(case_id, intent, str(index + 1), ttl_s)
    row = {"id": row_id, "title": _trim(title, WA_ROW_TITLE_MAX) or str(index + 1)}
    if description:
        row["description"] = _trim(description, WA_ROW_DESC_MAX)
    return row


def wa_text(text: str) -> dict:
    return {"type": "text", "text": {"preview_url": False, "body": _trim(text, WA_TEXT_MAX) or "-"}}


def wa_buttons(text: str, buttons: list) -> dict:
    return {"type": "interactive", "interactive": {
        "type": "button",
        "body": {"text": _trim(text, WA_BODY_MAX) or "-"},
        "action": {"buttons": buttons[:WA_MAX_BUTTONS]},
    }}


def wa_list(text: str, button_label: str, rows: list) -> dict:
    return {"type": "interactive", "interactive": {
        "type": "list",
        "body": {"text": _trim(text, WA_BODY_MAX) or "-"},
        "action": {"button": _trim(button_label, WA_BUTTON_MAX), "sections": [
            {"title": "Options", "rows": rows[:WA_MAX_ROWS]},
        ]},
    }}


def wa_template(name: str, body_params: list, button_payloads: list = ()) -> dict:
    """Template message. button_payloads fill the template's quick-reply buttons in order."""
    components = []
    if body_params:
        components.append({"type": "body", "parameters": [{"type": "text", "text": _one_line(p)} for p in body_params]})
    for index, payload in enumerate(button_payloads):
        components.append({"type": "button", "sub_type": "quick_reply", "index": str(index),
                           "parameters": [{"type": "payload", "payload": payload}]})
    return {"type": "template", "template": {
        "name": name, "language": {"code": WA_TEMPLATE_LANGUAGE}, "components": components,
    }}


def build_wa_message(intent: str, case_id: str, data: dict):
    """Free-form WhatsApp message (inside the 24h window): (message dict, plain-text summary)."""
    data = data if isinstance(data, dict) else {}
    pb_ttl = POSTBACK_TTL_S.get(intent, POSTBACK_TTL_DEFAULT_S)
    title = _pick(data, "title", "heading", "subject")
    body = (_pick(data, "body", "text", "message", "description", "summary")
            or DEFAULT_TEXT.get(intent)
            or "Update on your service request.")
    text = f"*{title}*\n{body}" if title else body
    summary = " - ".join(p for p in (title, body) if p)

    if intent == "CSAT":
        ratings = [(5, "5 - Great"), (4, "4 - Good"), (3, "3 - Okay"), (2, "2 - Poor"), (1, "1 - Very poor")]
        rows = [_wa_row(case_id, label, None, "CSAT", str(score), pb_ttl, i) for i, (score, label) in enumerate(ratings)]
        return wa_list(text, "Rate us", rows), summary

    # (label, description, intent, optionId)
    choices = []
    for i, opt in enumerate(_options(data)):
        label = fmt_slot(opt["slotStart"], opt["slotEnd"], label=False) if opt.get("slotStart") else opt["title"]
        desc = opt["description"]
        if opt.get("slotStart"):
            desc = f"{DISPLAY_TZ_LABEL}" + (f" - technician {opt['technicianId']}" if opt.get("technicianId") else "")
        choices.append((label, desc, opt["intent"] or _default_option_intent(intent, i), opt["id"]))
    if intent == "URGENT_ALERT" and not choices:
        choices.append(("See options", None, "SHOW_OPTIONS", "-"))
    if intent == "CONFIRMATION" and data.get("allowReschedule"):
        choices.append(("Reschedule", None, "RESCHEDULE", "-"))
    if intent == "APPOINTMENT_OPTIONS" and choices:
        choices.append(("None suit me", None, "NO_SLOT_FITS", "-"))
    if intent in ("URGENT_ALERT", "RECOVERY_OFFER", "CLARIFY", "APPOINTMENT_OPTIONS"):
        choices.append(("Talk to a person", None, "HUMAN", "-"))

    if intent == "APPOINTMENT_OPTIONS":
        summary = " | ".join(c[0] for c in choices[:-2]) or summary

    if not choices:
        return wa_text(text), summary
    fits_buttons = (len(choices) <= WA_MAX_BUTTONS and intent != "APPOINTMENT_OPTIONS"
                    and all(len(c[0]) <= WA_BUTTON_MAX for c in choices))
    if fits_buttons:
        return wa_buttons(text, [_wa_button(case_id, c[0], c[2], c[3], pb_ttl) for c in choices]), summary
    rows = [_wa_row(case_id, c[0], c[1], c[2], c[3], pb_ttl, i) for i, c in enumerate(choices[:WA_MAX_ROWS])]
    return wa_list(text, "Choose" if intent != "APPOINTMENT_OPTIONS" else "See times", rows), summary


# {{2}} of opsresolve_case_progress: what changed on the request, per intent (kept plain and specific for Meta's
# UTILITY categorisation; no marketing wording).
WA_PROGRESS_STEP = {
    "APPOINTMENT_OPTIONS": "please choose a time for the service visit",
    "CLARIFY": "we need one more detail from you to continue",
    "URGENT_ALERT": "an urgent issue needs your attention",
    "RECOVERY_OFFER": "recovery options are ready for you to review",
    "CONFIRMATION": "your visit details have been updated",
}


def build_wa_template(intent: str, case_id: str, data: dict):
    """Approved-template fallback (outside the 24h window): (name, body params, quick-reply payloads)."""
    data = data if isinstance(data, dict) else {}
    ttl = POSTBACK_TTL_DEFAULT_S
    if intent == "CSAT":
        return "opsresolve_service_feedback", [case_id], [
            sign_postback(case_id, "CSAT", "5", ttl), sign_postback(case_id, "CSAT", "3", ttl),
            sign_postback(case_id, "CSAT", "1", ttl)]
    if intent == "CONFIRMATION":
        cal = data.get("calendar") if isinstance(data.get("calendar"), dict) else {}
        start = cal.get("start") or cal.get("startTime") or data.get("slotStart")
        end = cal.get("end") or cal.get("endTime") or data.get("slotEnd")
        if _local_dt(start):
            return "opsresolve_visit_confirmed", [case_id, fmt_slot(start, end)], [
                sign_postback(case_id, "CONFIRM", "-", ttl), sign_postback(case_id, "RESCHEDULE", "-", ttl)]
    if intent == "ETA_UPDATE":
        eta = _pick(data, "eta", "etaAt", "arrivalAt", "expectedAt")
        if _local_dt(eta):
            return "opsresolve_technician_eta", [case_id, fmt_slot(eta)], []
    # Generic nudge (utility template: names the request and says what changed). The tap re-opens the window.
    step = WA_PROGRESS_STEP.get(intent, "there is new information on your request")
    return "opsresolve_case_progress", [case_id, step], [sign_postback(case_id, "SHOW_UPDATE", "-", ttl)]


def send_whatsapp(phone: str, message: dict) -> str:
    """SendWhatsAppMessage from the WABA number. Returns the provider message id."""
    payload = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": "+" + _digits(phone)}
    payload.update(message)
    resp = get_social().send_whatsapp_message(
        originationPhoneNumberId=WA_PHONE_NUMBER_ID,
        message=json.dumps(payload).encode(),
        metaApiVersion=WA_META_API_VERSION,
    )
    return resp["messageId"]


def wa_deliver(phone, build_freeform, build_template) -> dict:
    """Deliver one WhatsApp message: free-form inside the 24h window, else an approved template.

    Only allowlisted numbers get a real send; everything else is a DynamoDB-only record (deliveryMode SIMULATED).
    Returns {status, deliveryMode, summary, providerMessageId?, failureCode?, skipReason?, template?}.
    """
    out = {"status": "SENT", "deliveryMode": "SIMULATED", "summary": None}
    allowed = bool(_digits(phone)) and _digits(phone) in wa_allowlist()
    try:
        message, out["summary"] = build_freeform()
    except Exception as exc:  # pylint: disable=broad-except  (signing key unreachable)
        logger.error("WhatsApp content build failed: %s", exc)
        if allowed:
            out.update(status="FAILED", failureCode="BUILD_ERROR")
        return out
    if not _digits(phone):
        out["skipReason"] = "NO_PHONE_NUMBER"
        return out
    if not allowed:
        out["skipReason"] = "NOT_ALLOWLISTED"
        return out
    if not wa_window_is_open(phone):
        name, params, payloads = build_template()
        out["template"] = name
        if (name, WA_TEMPLATE_LANGUAGE) not in wa_approved_templates():
            out.update(status="NOT_DELIVERED", skipReason="TEMPLATE_NOT_APPROVED")
            logger.warning("WhatsApp window closed and template %s not approved: not sent to %s",
                           name, mask_phone(phone))
            return out
        message = wa_template(name, params, payloads)
    out["deliveryMode"] = "LIVE"
    try:
        out["providerMessageId"] = send_whatsapp(phone, message)
        logger.info("WhatsApp %s sent to %s", out.get("template") or message.get("type"), mask_phone(phone))
    except ClientError as exc:
        out.update(status="FAILED", failureCode=exc.response["Error"].get("Code", "SEND_ERROR"))
        logger.error("SendWhatsAppMessage failed (%s) to %s: %s", out["failureCode"], mask_phone(phone),
                     exc.response["Error"].get("Message", ""))
    return out


def wa_record(item: dict, outcome: dict) -> None:
    """Write the conversation record (+ the WAMSG# pointer the normalizer uses for delivery statuses)."""
    item = dict(item)
    item.update(channel="WHATSAPP", status=outcome["status"], deliveryMode=outcome["deliveryMode"])
    for key in ("providerMessageId", "failureCode", "skipReason", "template"):
        if outcome.get(key):
            item[key] = outcome[key]
    table = get_dynamodb().Table(TABLE_MESSAGES)
    table.put_item(Item=item)
    if outcome.get("providerMessageId"):
        table.put_item(Item={
            "pk": f"WAMSG#{outcome['providerMessageId']}", "sk": "META",
            "msgPk": item["pk"], "msgSk": item["sk"], "caseId": item.get("caseId"),
            "ttl": int(time.time()) + 30 * 86400,
        })


# --- Technician notifications (createServiceAppointment / reassignTechnician side effects) ---

def get_technician(technician_id: str) -> dict:
    pk = technician_id if technician_id.startswith("TECHNICIANS#") else f"TECHNICIANS#{technician_id}"
    try:
        return get_dynamodb().Table(TABLE_TECHNICIANS).get_item(Key={"pk": pk, "sk": "META"}).get("Item", {})
    except ClientError as exc:
        logger.error("Technician lookup failed: %s", exc)
        return {}


def _case_asset(case_id: str) -> str:
    try:
        item = get_dynamodb().Table(TABLE_CASES).get_item(
            Key={"pk": f"CASE#{case_id}", "sk": "META"}, ProjectionExpression="assetId").get("Item") or {}
        return item.get("assetId") or "-"
    except ClientError as exc:
        logger.warning("Case lookup failed for %s: %s", case_id, exc)
        return "-"


def notify_technician(kind: str, technician_id: str, case_id: str, work_order_id: str,
                      slot_start=None, slot_end=None, idempotency_key: str = "", extra: dict = None) -> dict:
    """WhatsApp the technician about a job. kind = ASSIGNED | RELEASED | PART_APPROVED.
    Never raises (the tool result stands)."""
    try:
        tech = get_technician(technician_id)
        phone = tech.get("phone")
        ttl = POSTBACK_TTL_DEFAULT_S
        slot = fmt_slot(slot_start, slot_end) if slot_start else "-"
        extra = extra or {}
        if kind == "PART_APPROVED":
            part_id, qty, depot = extra.get("partId") or "-", extra.get("qty") or 1, extra.get("depotId") or "-"
            eta = fmt_slot(extra["etaAt"]) if extra.get("etaAt") else None
            where = f"transfer to {depot} arriving {eta}" if eta else f"reserved at {depot}"

            def freeform():
                text = (f"*Part request approved: {case_id}*\nPart: {part_id} x{qty}\n"
                        f"Work order: {work_order_id}\nStock: {where}.")
                return wa_text(text), f"Part approved: {part_id} x{qty}, {where}"

            def template():
                # No approved template for this message: outside the 24h window it is recorded only.
                return "opsresolve_part_approved", [case_id, part_id, where], []
            intent = "PART_APPROVED"
        elif kind == "ASSIGNED":
            asset = _case_asset(case_id)

            def freeform():
                text = (f"*New job {case_id}*\nAsset: {asset}\nSlot: {slot}\nWork order: {work_order_id}\n"
                        "Please confirm you can attend.")
                return wa_buttons(text, [
                    _wa_button(case_id, "Accept", "TECH_ACCEPT", work_order_id, ttl),
                    _wa_button(case_id, "Can't make it", "TECH_DECLINE", work_order_id, ttl),
                ]), f"Job assigned: {case_id}, {asset}, {slot}"

            def template():
                return "opsresolve_job_assigned", [case_id, asset, slot], [
                    sign_postback(case_id, "TECH_ACCEPT", work_order_id, ttl),
                    sign_postback(case_id, "TECH_DECLINE", work_order_id, ttl)]
            intent = "JOB_ASSIGNED"
        else:
            def freeform():
                text = (f"Job {case_id} (work order {work_order_id}) has been reassigned to another technician. "
                        "No action is needed from you.")
                return wa_text(text), f"Job released: {case_id}"

            def template():
                return "opsresolve_job_reassigned", [case_id], []
            intent = "JOB_RELEASED"

        outcome = wa_deliver(phone, freeform, template)
        sent_at = now_iso()
        message_id = new_id("msg-")
        wa_record({
            "pk": f"CASE#{case_id}",
            "sk": f"MSG#{sent_at}#{message_id}",
            "messageId": message_id,
            "caseId": case_id,
            "recipientType": "TECHNICIAN",
            "technicianId": technician_id,
            "workOrderId": work_order_id,
            "direction": "OUTBOUND",
            "intent": intent,
            "body": outcome.get("summary") or "",
            "idempotencyKey": idempotency_key,
            "sentAt": sent_at,
        }, outcome)
        result = {"technicianId": technician_id, "channel": "WHATSAPP", "messageId": message_id,
                  "status": outcome["status"], "deliveryMode": outcome["deliveryMode"]}
        if outcome.get("skipReason"):
            result["skipReason"] = outcome["skipReason"]
        return result
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Technician notification failed for %s: %s", technician_id, exc)
        return {"technicianId": technician_id, "channel": "WHATSAPP", "status": "FAILED",
                "failureCode": "NOTIFY_ERROR"}


def handle_send_customer_message(args: dict) -> dict:
    correlation_id = args.get("correlationId")

    err = validate_required(args, ["caseId", "contactId", "intent", "data", "idempotencyKey"], correlation_id)
    if err:
        return err

    valid_intents = set(CAPABLE.keys()) | {"URGENT_ALERT"}
    if args["intent"] not in valid_intents:
        return error_envelope("VALIDATION_ERROR", f"Unknown intent: {args['intent']}", correlation_id)

    hit, cached = check_idempotency(args["idempotencyKey"], "sendCustomerMessage", args)
    if hit:
        return cached

    contact_id = args["contactId"]
    intent = args["intent"]

    # Fetch contact preferences from DynamoDB (mock-contacts: pk="CONTACTS#<id>", sk="META")
    contact = get_contact(contact_id)
    contact_id = contact_id.split("#", 1)[-1]  # bare id ("CON-77") stored on the message record

    consent = contact.get("consent", {})
    preferred = contact.get("preferredChannel", "SMS")
    active_channel = contact.get("activeChannel")
    # 24h customer-service window: tracked from real inbound WhatsApp messages (normalizer), else the mock flag
    wa_window_open = wa_window_is_open(contact.get("phone")) or bool(contact.get("waWindowOpen", False))
    priority = contact.get("priority", "P3")

    # Default consent: allow all if no contact record (demo mode)
    if not consent:
        consent = {c: "OPTED_IN" for c in ORDER}

    channels = select_channel(intent, consent, preferred, active_channel, wa_window_open, priority)

    if not channels:
        return error_envelope(
            "CHANNEL_NOT_CONSENTED",
            f"No consented channel available for intent {intent} and contact {contact_id}",
            correlation_id,
        )

    messages_table = get_dynamodb().Table(TABLE_MESSAGES)
    case_id = args["caseId"]
    sent_channels = []
    delivery_modes = []
    failures = []
    last_message_id = None

    for channel in channels:
        message_id = new_id("msg-")
        sent_at = now_iso()
        status = "SENT"
        delivery_mode = "SIMULATED"   # DynamoDB record only
        provider_message_id = None
        failure_code = None
        body_text = _pick(args["data"], "body", "text", "message") if isinstance(args["data"], dict) else None

        if channel == "RCS":
            content = ttl = None
            try:
                content, ttl, body_text = build_rcs_content(intent, case_id, args["data"])
            except Exception as exc:  # pylint: disable=broad-except
                # Typically the signing key is unreachable. Fatal for a real send, harmless when simulating.
                logger.error("RCS content build failed: %s", exc)
                if RCS_SEND_ENABLED:
                    status, failure_code = "FAILED", "BUILD_ERROR"

            if RCS_SEND_ENABLED and status != "FAILED":
                delivery_mode = "LIVE"
                phone = contact.get("phone")
                if not phone:
                    status, failure_code = "FAILED", "NO_PHONE_NUMBER"
                else:
                    try:
                        provider_message_id = send_rcs(phone, content, ttl, case_id, intent)
                    except ClientError as exc:
                        status = "FAILED"
                        failure_code = exc.response["Error"].get("Code", "SEND_ERROR")
                        logger.error("SendRcsMessage failed (%s) for message %s", failure_code, message_id)

        if channel == "WHATSAPP":
            data = args["data"]
            outcome = wa_deliver(
                contact.get("phone"),
                lambda: build_wa_message(intent, case_id, data),
                lambda: build_wa_template(intent, case_id, data),
            )
            wa_record({
                "pk": f"CASE#{case_id}",
                "sk": f"MSG#{sent_at}#{message_id}",
                "messageId": message_id,
                "caseId": case_id,
                "contactId": contact_id,
                "recipientType": "CUSTOMER",
                "direction": "OUTBOUND",
                "intent": intent,
                "body": outcome.get("summary") or body_text or "",
                "data": json.dumps(data),
                "idempotencyKey": args["idempotencyKey"],
                "sentAt": sent_at,
            }, outcome)
            if outcome["status"] == "FAILED":
                failures.append(outcome.get("failureCode"))
                continue
            last_message_id = message_id
            sent_channels.append(channel)
            delivery_modes.append(outcome["deliveryMode"] if outcome["status"] == "SENT" else outcome.get("skipReason"))
            continue

        # conversations table: pk = CASE#<caseId>, sk = MSG#<ts>#<messageId>  (one query lists a case's messages)
        item = {
            "pk": f"CASE#{case_id}",
            "sk": f"MSG#{sent_at}#{message_id}",
            "messageId": message_id,
            "caseId": case_id,
            "contactId": contact_id,
            "direction": "OUTBOUND",
            "intent": intent,
            "channel": channel,
            "body": body_text or "",
            "data": json.dumps(args["data"]),
            "status": status,
            "deliveryMode": delivery_mode,
            "idempotencyKey": args["idempotencyKey"],
            "sentAt": sent_at,
        }
        if provider_message_id:
            item["providerMessageId"] = provider_message_id
        if failure_code:
            item["failureCode"] = failure_code
        messages_table.put_item(Item=item)

        if status == "FAILED":
            failures.append(failure_code)
            continue
        last_message_id = message_id
        sent_channels.append(channel)
        delivery_modes.append(delivery_mode)

    if not sent_channels:
        # Not cached in the idempotency table, so the agent can retry with the same key.
        retryable = any(code in ("ThrottlingException", "InternalServerException") for code in failures)
        return error_envelope(
            "CHANNEL_UNAVAILABLE",
            f"Message could not be sent on {channels}: {failures}",
            correlation_id,
            details={"failureCodes": failures},
            retryable=retryable,
        )

    # For URGENT_ALERT P1 dual-send, return first channel (both were sent)
    result = {
        "messageId": last_message_id,
        "channel": sent_channels[0],
        "status": "SENT",
        # LIVE = really delivered to the provider; SIMULATED = DynamoDB record only;
        # TEMPLATE_NOT_APPROVED = WhatsApp window closed and no approved template yet (recorded, not sent)
        "deliveryMode": delivery_modes[0],
    }
    save_idempotency(args["idempotencyKey"], "sendCustomerMessage", args, result)
    return result


def handle_request_approval(args: dict) -> dict:
    correlation_id = args.get("correlationId")

    err = validate_required(args, ["caseId", "action", "approverRole", "idempotencyKey"], correlation_id)
    if err:
        return err

    hit, cached = check_idempotency(args["idempotencyKey"], "requestApproval", args)
    if hit:
        return cached

    approval_id = new_id("appr-")
    notified_approver = f"{args['approverRole']}@opsresolve.internal"

    approvals_table = get_dynamodb().Table(TABLE_APPROVALS)
    approvals_table.put_item(Item={
        "approvalId": approval_id,
        "caseId": args["caseId"],
        "action": args["action"],
        "amount": str(args.get("amount", "")),
        "approverRole": args["approverRole"],
        "notifiedApprover": notified_approver,
        "status": "PENDING",
        "idempotencyKey": args["idempotencyKey"],
        "createdAt": now_iso(),
    })

    result = {
        "approvalId": approval_id,
        "status": "PENDING",
        "notifiedApprover": notified_approver,
    }
    save_idempotency(args["idempotencyKey"], "requestApproval", args, result)
    return result


# ---------------------------------------------------------------------------
# applyServiceCredit / dispatchCourier (Scenario 2)
# ---------------------------------------------------------------------------

SELF_INSTALL_STEPS = (  # knowledge/s2/ONT-X200_self_install_guide.md
    "1. Power off the old ONT. 2. Move the green fibre connector to the new ONT - do not look into the fibre. "
    "3. Move the power and LAN cables. 4. Power on; wait for the PON light to turn solid green (up to 5 minutes). "
    "5. Tap Installed in WhatsApp."
)


def _credit_policy(case_id: str, customer_id: str) -> dict:
    """``serviceCredit`` of the contract covering the case's asset ({} when none is found)."""
    asset_id = _case_asset(case_id)
    try:
        rows = get_dynamodb().Table(TABLE_CONTRACTS).scan().get("Items", [])
    except ClientError as exc:
        logger.error("Contract lookup failed: %s", exc)
        return {}
    for row in rows:
        if row.get("customerId") == customer_id and asset_id in (row.get("assetIds") or []):
            return row.get("serviceCredit") or {}
    return {}


def _credit_approved(approval: dict, case_id: str, amount: Decimal) -> bool:
    action = str((approval or {}).get("action") or "")
    prefix = "SERVICE_CREDIT_OVER_CAP:"
    if (approval or {}).get("status") != "APPROVED" or approval.get("caseId") != case_id \
            or not action.startswith(prefix):
        return False
    try:
        return Decimal(action[len(prefix):]) == amount
    except ArithmeticError:
        return False


def handle_apply_service_credit(args: dict) -> dict:
    """Apply a downtime credit: up to the contract's autoApplyCapUsd on its own, above it only with an APPROVED
    approval for this case and the exact amount. Otherwise NEEDS_APPROVAL and nothing is written."""
    correlation_id = args.get("correlationId")
    err = validate_required(args, ["caseId", "customerId", "amountUsd", "idempotencyKey"], correlation_id)
    if err:
        return err
    try:
        amount = Decimal(str(args["amountUsd"]))
    except ArithmeticError:
        return error_envelope("VALIDATION_ERROR", "amountUsd must be a number", correlation_id)
    if amount <= 0:
        return error_envelope("VALIDATION_ERROR", "amountUsd must be positive", correlation_id)

    hit, cached = check_idempotency(args["idempotencyKey"], "applyServiceCredit", args)
    if hit:
        return cached

    case_id = args["caseId"]
    policy = _credit_policy(case_id, args["customerId"])
    if not policy:
        return error_envelope("NOT_FOUND", f"No service credit policy for case {case_id}", correlation_id)
    cap = Decimal(str(policy.get("autoApplyCapUsd", 0)))
    if amount > cap:
        approval = None
        if args.get("approvalId"):
            try:
                approval = get_dynamodb().Table(TABLE_APPROVALS).get_item(
                    Key={"approvalId": args["approvalId"]}).get("Item")
            except ClientError as exc:
                logger.error("Approval lookup failed: %s", exc)
                return error_envelope("DOWNSTREAM_TIMEOUT", "Approvals unavailable", correlation_id, retryable=True)
        if not _credit_approved(approval, case_id, amount):
            return {"status": "NEEDS_APPROVAL", "amountUsd": float(amount), "autoApplyCapUsd": float(cap)}

    credit_id = new_id("crd-")
    spec = {"creditId": credit_id, "amountUsd": amount, "status": "APPLIED"}
    table = get_dynamodb().Table(TABLE_CASES)
    table.put_item(Item={
        "pk": f"CASE#{case_id}", "sk": f"CREDIT#{credit_id}", "caseId": case_id, "customerId": args["customerId"],
        "reason": args.get("reason") or "Service downtime credit", "appliedAt": now_iso(),
        **({"approvalId": args["approvalId"]} if args.get("approvalId") else {}), **spec,
    })
    table.update_item(
        Key={"pk": f"CASE#{case_id}", "sk": "META"}, UpdateExpression="SET serviceCredit = :c",
        ConditionExpression="attribute_exists(pk)", ExpressionAttributeValues={":c": spec},
    )
    result = {"creditId": credit_id, "status": "APPLIED", "amountUsd": float(amount)}
    save_idempotency(args["idempotencyKey"], "applyServiceCredit", args, result)
    return result


def handle_dispatch_courier(args: dict) -> dict:
    """Send a replacement part from a courier hub to the customer's site (mock courier): records the job in the
    transfers table (type COURIER), then tells the customer on WhatsApp (ETA + handover code) and by email (the
    self-install guide). A failed notification is reported in ``notifications``, never fails the dispatch."""
    correlation_id = args.get("correlationId")
    err = validate_required(args, ["caseId", "partId", "fromDepotId", "idempotencyKey"], correlation_id)
    if err:
        return err
    hit, cached = check_idempotency(args["idempotencyKey"], "dispatchCourier", args)
    if hit:
        return cached
    try:
        couriers = get_dynamodb().Table(TABLE_COURIERS).scan().get("Items", [])
    except ClientError as exc:
        logger.error("Courier lookup failed: %s", exc)
        return error_envelope("DOWNSTREAM_TIMEOUT", "Couriers unavailable", correlation_id, retryable=True)
    if not couriers:
        return error_envelope("NOT_FOUND", "No courier available", correlation_id)
    courier = sorted(couriers, key=lambda c: str(c.get("courierId")))[0]

    case_id = args["caseId"]
    eta_min = int(courier.get("etaMin", 150))
    code = str(pysecrets.randbelow(900000) + 100000)
    job_id = new_id("crj-")
    get_dynamodb().Table(TABLE_TRANSFERS).put_item(Item={
        "transferId": job_id, "type": "COURIER", "caseId": case_id, "partId": args["partId"],
        "fromDepotId": args["fromDepotId"], "courierId": courier.get("courierId"), "etaMin": eta_min,
        "handoverCode": code, "status": "DISPATCHED", "idempotencyKey": args["idempotencyKey"],
        "createdAt": now_iso(),
    })

    notifications = {}
    contact_id = args.get("contactId")
    if contact_id:
        text = (f"Your replacement fibre box is on its way with {courier.get('name', 'our courier')}. "
                f"Expected in about {eta_min} minutes. Give the courier this handover code: {code}.")
        notifications["customerMessage"] = handle_send_customer_message({
            "caseId": case_id, "contactId": contact_id, "intent": "ETA_UPDATE", "data": {"body": text},
            "idempotencyKey": f"{args['idempotencyKey']}:eta", "correlationId": correlation_id,
        })
        # Demo: the synthetic subscribers' addresses are not deliverable, so mail goes to the demo recipient.
        notifications["email"] = handle_send_case_update_email({
            "caseId": case_id, "recipientEmail": SES_DEFAULT_RECIPIENT, "updateType": "SCHEDULED",
            "subject": "Your replacement fibre box is on its way", "summary": text,
            "details": {"Courier": courier.get("name"), "Expected in (minutes)": eta_min,
                        "Handover code": code, "Self-install steps": SELF_INSTALL_STEPS},
            "idempotencyKey": f"{args['idempotencyKey']}:guide", "correlationId": correlation_id,
        })
    result = {"courierJobId": job_id, "courierId": courier.get("courierId"), "etaMin": eta_min,
              "handoverCode": code, "status": "DISPATCHED", "notifications": notifications}
    save_idempotency(args["idempotencyKey"], "dispatchCourier", args, result)
    return result


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

DISPATCH = {
    "executeSafeRemoteAction":          handle_execute_safe_remote_action,
    "generateServiceReport":            handle_generate_service_report,
    "closeServiceCase":                 handle_close_service_case,
    "retrieveTroubleshootingKnowledge": handle_retrieve_troubleshooting_knowledge,
    "findAndReservePart":               handle_find_and_reserve_part,
    "releasePartReservation":           handle_release_part_reservation,
    "transferPart":                     handle_transfer_part,
    "proposeAppointmentSlots":          handle_propose_appointment_slots,
    "createServiceAppointment":         handle_create_service_appointment,
    "reassignTechnician":               handle_reassign_technician,
    "updateWorkOrder":                  handle_update_work_order,
    "sendCustomerMessage":              handle_send_customer_message,
    "requestApproval":                  handle_request_approval,
    "applyServiceCredit":               handle_apply_service_credit,
    "dispatchCourier":                  handle_dispatch_courier,
}

# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------

def lambda_handler(event, context):
    """
    AgentCore Gateway dispatch.
    Tool name comes from context.client_context.custom['bedrockAgentCoreToolName'].
    Each tool has its own if/elif block with explicit argument unpacking.
    """
    print(event)
    logger.info("Event: %s", json.dumps(event))

    # Extract tool name from AgentCore Gateway context
    try:
        tool_name = context.client_context.custom.get("bedrockAgentCoreToolName", "unknown")
    except (AttributeError, TypeError):
        # Fallback for local testing: read tool_name from event directly
        tool_name = event.get("tool_name", "unknown")

    logger.info("Tool name: %s", tool_name)

    try:

        if "executeSafeRemoteAction" in tool_name:
            result = handle_execute_safe_remote_action({
                "caseId":          event["caseId"],
                "assetId":         event["assetId"],
                "action":          event["action"],
                "approvalId":      event.get("approvalId"),
                "idempotencyKey":  event["idempotencyKey"],
                "correlationId":   event.get("correlationId"),
            })

        elif "generateServiceReport" in tool_name:
            result = handle_generate_service_report({
                "caseId":          event["caseId"],
                "format":          event["format"],
                "idempotencyKey":  event["idempotencyKey"],
                "correlationId":   event.get("correlationId"),
            })

        elif "closeServiceCase" in tool_name:
            result = handle_close_service_case({
                "caseId":             event["caseId"],
                "workOrderId":        event.get("workOrderId"),
                "verificationResult": event["verificationResult"],
                "idempotencyKey":     event["idempotencyKey"],
                "correlationId":      event.get("correlationId"),
            })

        elif "retrieveTroubleshootingKnowledge" in tool_name:
            result = handle_retrieve_troubleshooting_knowledge({
                "query":       event["query"],
                "assetModel":  event.get("assetModel"),
                "firmware":    event.get("firmware"),
                "topK":        event.get("topK", 6),
                "correlationId": event.get("correlationId"),
            })

        elif "findAndReservePart" in tool_name:
            result = handle_find_and_reserve_part({
                "caseId":         event["caseId"],
                "partId":         event["partId"],
                "depotId":        event.get("depotId"),
                "mode":           event["mode"],
                "approvalId":     event.get("approvalId"),
                "idempotencyKey": event["idempotencyKey"],
                "correlationId":  event.get("correlationId"),
            })

        elif "releasePartReservation" in tool_name:
            result = handle_release_part_reservation({
                "reservationId":  event["reservationId"],
                "idempotencyKey": event["idempotencyKey"],
                "correlationId":  event.get("correlationId"),
            })

        elif "transferPart" in tool_name:
            result = handle_transfer_part({
                "partId":         event["partId"],
                "fromDepotId":    event["fromDepotId"],
                "toDepotId":      event["toDepotId"],
                "quantity":       event["quantity"],
                "idempotencyKey": event["idempotencyKey"],
                "correlationId":  event.get("correlationId"),
            })

        elif "proposeAppointmentSlots" in tool_name:
            result = handle_propose_appointment_slots({
                "caseId":         event["caseId"],
                "technicianIds":  event["technicianIds"],
                "durationMin":    event["durationMin"],
                "windowStart":    event["windowStart"],
                "windowEnd":      event["windowEnd"],
                "correlationId":  event.get("correlationId"),
            })

        elif "createServiceAppointment" in tool_name:
            result = handle_create_service_appointment({
                "caseId":         event["caseId"],
                "technicianId":   event["technicianId"],
                "slotStart":      event["slotStart"],
                "slotEnd":        event["slotEnd"],
                "consentRef":     event["consentRef"],
                "idempotencyKey": event["idempotencyKey"],
                "correlationId":  event.get("correlationId"),
            })

        elif "reassignTechnician" in tool_name:
            result = handle_reassign_technician({
                "workOrderId":      event["workOrderId"],
                "newTechnicianId":  event["newTechnicianId"],
                "reason":           event["reason"],
                "idempotencyKey":   event["idempotencyKey"],
                "correlationId":    event.get("correlationId"),
            })

        elif "updateWorkOrder" in tool_name:
            result = handle_update_work_order({
                "workOrderId":    event["workOrderId"],
                "status":         event.get("status"),
                "notes":          event.get("notes"),
                "partsUsed":      event.get("partsUsed"),
                "idempotencyKey": event["idempotencyKey"],
                "correlationId":  event.get("correlationId"),
            })

        elif "sendCustomerMessage" in tool_name:
            result = handle_send_customer_message({
                "caseId":         event["caseId"],
                "contactId":      event["contactId"],
                "intent":         event["intent"],
                "data":           event["data"],
                "idempotencyKey": event["idempotencyKey"],
                "correlationId":  event.get("correlationId"),
            })
            
        elif "sendCaseUpdateEmail" in tool_name:
            result = handle_send_case_update_email({
                "caseId":          event["caseId"],
                "recipientEmail":  event["recipientEmail"],
                "updateType":      event["updateType"],
                "subject":         event["subject"],
                "summary":         event["summary"],
                "details":         event.get("details", {}),
                "idempotencyKey":  event["idempotencyKey"],
                "correlationId":   event.get("correlationId"),
            })

        elif "applyServiceCredit" in tool_name:
            result = handle_apply_service_credit({
                "caseId":         event["caseId"],
                "customerId":     event["customerId"],
                "amountUsd":      event["amountUsd"],
                "reason":         event.get("reason"),
                "approvalId":     event.get("approvalId"),
                "idempotencyKey": event["idempotencyKey"],
                "correlationId":  event.get("correlationId"),
            })

        elif "dispatchCourier" in tool_name:
            result = handle_dispatch_courier({
                "caseId":         event["caseId"],
                "partId":         event["partId"],
                "fromDepotId":    event["fromDepotId"],
                "contactId":      event.get("contactId"),
                "idempotencyKey": event["idempotencyKey"],
                "correlationId":  event.get("correlationId"),
            })

        elif "requestApproval" in tool_name:
            result = handle_request_approval({
                "caseId":         event["caseId"],
                "action":         event["action"],
                "amount":         event.get("amount"),
                "approverRole":   event["approverRole"],
                "idempotencyKey": event["idempotencyKey"],
                "correlationId":  event.get("correlationId"),
            })

        else:
            return {
                "statusCode": 200,
                "body": json.dumps({"message": f"Unknown tool: '{tool_name}'"}),
            }

        logger.info("Result for %s: %s", tool_name, json.dumps(result))
        return {
            "statusCode": 200,
            "body": json.dumps(result),
        }

    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Unhandled error in %s: %s", tool_name, exc)
        return {
            "statusCode": 200,
            "body": json.dumps(
                error_envelope(
                    "DOWNSTREAM_TIMEOUT",
                    f"Internal error processing {tool_name}: {str(exc)}",
                    event.get("correlationId"),
                    retryable=True,
                )
            ),
        }
