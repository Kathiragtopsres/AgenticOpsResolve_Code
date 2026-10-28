"""Inbound Normalizer Lambda (Section 7.7).

Consumes the inbound SQS queue (RCS agent two-way -> SNS ``agentic-opsresolve-topic`` -> SQS
``opsresolve-dev-inbound``) and, per record:
  - parses the RCS payload (SNS envelope -> End User Messaging message -> messageBody);
  - drops RCS typing events;
  - verifies the signed postback on button taps (inbound.postback.verify) and enforces
    single use (case_store.consume_postback_nonce); forged / expired / replayed taps are
    logged as security events and dropped, never sent to the agent;
  - resolves the contact (by sender phone) and the active case;
  - runs Bedrock Guardrails on free text;
  - puts one normalised event on ``opsresolve-<env>-bus``:
        ``Customer Action Selected``  (button tap)  or  ``Customer Message Received`` (typed text).

The event detail carries the normalised fields (schemas/channels/normalized-inbound.schema.json)
under ``payload``; ``eventId`` is derived from the provider message id / nonce so SQS redelivery
produces the same eventId and the Agent Invoker's event idempotency drops the duplicate.

Messages with no active case still go through with ``caseId`` omitted and ``payload.customerId`` set:
the orchestrator opens a new case for them (Journey A). Messages from unknown senders are dropped.

WhatsApp (WABA event destination -> SNS ``opsresolve-dev-whatsapp-events`` -> the same SQS queue): records whose
message carries ``whatsAppWebhookEntry`` are handled by the WhatsApp section below. Per webhook message:
  - every inbound message refreshes the 24h customer-service window (conversations table,
    pk=``WAUSER#<sha256(digits)[:24]>``, sk=``WINDOW``; read by the Gateway Lambda before free-form sends);
  - customer text / media -> Guardrails -> ``Customer Message Received``; button / list taps (signed postbacks) ->
    ``Customer Action Selected``;
  - technician taps (intents TECH_ACCEPT / TECH_DECLINE, sender matched in mock-technicians): Accept is recorded only;
    "Can't make it" -> ``Technician ETA Updated`` (source workforce) with ``available: false``. Technician free text is
    recorded only;
  - delivery ``statuses`` update the outbound message record (via the ``WAMSG#<id>`` pointer); no agent event;
  - every inbound WhatsApp message is also written to the conversations table (direction INBOUND).

Handler: opsresolve.inbound.normalizer.handler
Env: OPSRESOLVE_ENV, GUARDRAIL_ID, GUARDRAIL_VERSION, POSTBACK_KEY_SECRET_ID
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from ..common import case_store, tenants
from ..common.config import settings
from ..common.dynamo import get_table
from ..common.events import DetailType, EventSource, build_envelope
from ..common.logging import get_logger, mask_phone
from . import postback

logger = get_logger(__name__)

GUARDRAIL_ID = os.getenv("GUARDRAIL_ID", "xa0zjafyaz7z")  # opsreslove-guardrail
GUARDRAIL_VERSION = os.getenv("GUARDRAIL_VERSION", "DRAFT")
POSTBACK_KEY_SECRET_ID = os.getenv("POSTBACK_KEY_SECRET_ID", "opsresolve/dev/postback-hmac-key")

REDACTED_TEXT = "[redacted by guardrail]"
OPT_OUT_KEYWORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
TERMINAL_STATES = {"CLOSED", "CANCELLED"}


class Drop(Exception):
    """Record is valid but must not reach the agent (typing event, forged tap, unknown sender...)."""


# --- AWS clients (lazy, import-safe) ---------------------------------------------------


@lru_cache(maxsize=1)
def _events():  # pragma: no cover - thin SDK wrapper
    import boto3

    return boto3.client("events", region_name=settings.region)


@lru_cache(maxsize=1)
def _bedrock():  # pragma: no cover - thin SDK wrapper
    import boto3

    return boto3.client("bedrock-runtime", region_name=settings.region)


@lru_cache(maxsize=1)
def _postback_key() -> bytes:  # pragma: no cover - thin SDK wrapper
    import boto3

    resp = boto3.client("secretsmanager", region_name=settings.region).get_secret_value(
        SecretId=POSTBACK_KEY_SECRET_ID
    )
    return resp["SecretString"].encode()


# --- Entry point -----------------------------------------------------------------------


def handler(event: dict, context: object | None = None) -> dict:
    """Lambda entry point. Processes one or more SQS records; failed ones are retried (then DLQ)."""
    failures = []
    tenants.reset_cache()
    for record in event.get("Records", []):
        message_id = record.get("messageId") or record.get("MessageId") or ""
        try:
            for entry in normalize_record_entries(record):
                _publish(entry)
        except Exception:  # noqa: BLE001 - one bad record must not fail the whole batch
            logger.exception("inbound record failed: %s", message_id)
            failures.append({"itemIdentifier": message_id})
    return {"batchItemFailures": failures}


def normalize_record_entries(record: dict) -> list[dict]:
    """All EventBridge entries for one record (a WhatsApp webhook can hold several messages)."""
    try:
        msg, _ts = _message(record)
    except Drop as why:
        logger.info("inbound dropped: %s", why)
        return []
    if "whatsAppWebhookEntry" in msg:
        return _normalize_whatsapp(msg)
    entry = normalize_record(record)
    return [entry] if entry is not None else []


def normalize_record(record: dict) -> dict | None:
    """Parse one RCS record into an EventBridge entry (``{"detailType", "detail"}``), or None to drop.

    Raises on transient failures (Guardrails / DynamoDB / Secrets Manager) so the record is retried.
    """
    try:
        return _normalize(record)
    except Drop as why:
        logger.info("inbound dropped: %s", why)
        return None


# --- Parsing ---------------------------------------------------------------------------


def _message(record: dict) -> tuple[dict, str | None]:
    """(End User Messaging message dict, SNS timestamp) from an SQS record or an SNS record."""
    if "Sns" in record:
        raw, ts = record["Sns"].get("Message"), record["Sns"].get("Timestamp")
    else:
        body = record.get("body")
        body = json.loads(body) if isinstance(body, str) else (body or {})
        raw, ts = body.get("Message", body), body.get("Timestamp")
    msg = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(msg, dict):
        raise Drop("unparseable message")
    return msg, ts


def _iso(ts: str | None) -> str:
    if ts:
        try:
            parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _split_body(message_body: Any) -> tuple[str, dict]:
    """RCS replies arrive as plain text, or as a JSON string with a ``type``.

    Returns (type, fields): type is TEXT | SUGGESTION | RCS_TYPING | <other RCS type>.
    """
    if isinstance(message_body, str) and message_body.lstrip().startswith("{"):
        try:
            parsed = json.loads(message_body)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict) and parsed.get("type"):
            return str(parsed["type"]).upper(), parsed
    return "TEXT", {"text": message_body if isinstance(message_body, str) else ""}


def _normalize(record: dict) -> dict:
    msg, sns_ts = _message(record)
    origination = msg.get("originationNumber") or ""
    destination = msg.get("destinationNumber") or ""
    provider_id = msg.get("inboundMessageId") or ""

    if not str(destination).startswith("rcs-"):
        raise Drop("not an RCS message")

    body_type, fields = _split_body(msg.get("messageBody"))
    if "TYPING" in body_type:
        raise Drop("typing event")

    received_at = _iso(sns_ts)
    contact = _contact_by_phone(origination)
    contact_id = contact["contactId"] if contact else None
    customer_id = contact.get("customerId") if contact else None

    payload: dict[str, Any] = {
        "channel": "RCS",
        "contactId": contact_id,
        "caseId": None,
        "kind": "TEXT",
        "intent": None,
        "optionId": None,
        "text": None,
        "receivedAt": received_at,
        "providerMessageId": provider_id,
        "verified": True,
        "guardrail": {"action": "NONE"},
    }

    if body_type == "SUGGESTION":
        pb = _verify_postback(fields.get("postbackData") or "", provider_id, origination)
        meta = _existing_case(pb["caseId"])
        payload.update(
            caseId=pb["caseId"], kind="POSTBACK", intent=pb["intent"], optionId=pb["optionId"],
            text=fields.get("text"),
        )
        customer_id = customer_id or meta.customer_id
        event_key = f"pb-{pb['nonce']}"
        detail_type = DetailType.CUSTOMER_ACTION_SELECTED
    else:
        if contact is None:
            raise Drop(f"unknown sender {mask_phone(origination)}")
        text = (fields.get("text") or fields.get("body") or "").strip()
        if not text:
            # Non-text RCS content (location, file, ...): pass a MEDIA marker, nothing to scan.
            payload["kind"] = "MEDIA"
        elif text.upper() in OPT_OUT_KEYWORDS:
            payload.update(kind="OPT_OUT", text=text)
        else:
            payload["text"], payload["guardrail"] = _apply_guardrail(text)
        meta = _active_case(contact)
        payload["caseId"] = meta.case_id if meta else None
        customer_id = customer_id or (meta.customer_id if meta else None)
        event_key = f"in-{hashlib.sha1(provider_id.encode()).hexdigest()[:16]}"
        detail_type = DetailType.CUSTOMER_MESSAGE_RECEIVED

    if customer_id:
        payload["customerId"] = customer_id
    event_id = f"evt-{event_key}"
    detail = {
        "eventId": event_id,
        "eventVersion": "1.0",
        "occurredAt": received_at,
        "correlationId": event_id,
        "actor": {"type": "CUSTOMER", "id": contact_id or "UNKNOWN"},
        "payload": payload,
    }
    if payload["caseId"]:
        detail["caseId"] = payload["caseId"]
    logger.info(
        "inbound %s from %s case=%s intent=%s", payload["kind"], mask_phone(origination),
        payload["caseId"], payload["intent"],
    )
    return {"detailType": detail_type, "detail": detail}


# --- Postback verification -------------------------------------------------------------


def _verify_postback(value: str, provider_id: str, origination: str) -> dict:
    """Signature + expiry via inbound.postback.verify, then single-use via the nonce store."""
    try:
        pb = postback.verify(_postback_key(), value)
    except postback.PostbackError as exc:
        _security_event(str(exc), provider_id, origination)
        raise Drop(f"postback rejected: {exc}") from exc
    if not case_store.consume_postback_nonce(pb["nonce"]):
        _security_event("REPLAYED", provider_id, origination)
        raise Drop("postback rejected: REPLAYED")
    return pb


def _security_event(reason: str, provider_id: str, origination: str) -> None:
    logger.warning(
        "SECURITY_EVENT POSTBACK_%s providerMessageId=%s sender=%s", reason, provider_id, mask_phone(origination)
    )


def _release_nonce(nonce: str) -> None:
    """Undo consume_postback_nonce when the event could not be published, so the retry is not seen as a replay."""
    get_table(settings.idempotency_table).delete_item(Key={"pk": f"NONCE#{nonce}", "sk": "META"})


# --- Contact / case resolution ---------------------------------------------------------


def _prefer(items: list[dict], has_open_case) -> dict:
    """Pick one of several records that share a phone (the demo reuses one handset per role across tenants):
    the record of the active demo tenant (missing tenantId = the default tenant), then one with an open case,
    else the first."""
    if len(items) > 1:
        active = tenants.active_demo_tenant()
        in_tenant = [i for i in items if (i.get("tenantId") or tenants.DEFAULT_TENANT) == active]
        pool = in_tenant or items
        items = [i for i in pool if has_open_case(i)] or pool
    return items[0]


def _contact_by_phone(phone: str) -> dict | None:
    """mock-contacts has a handful of items; a filtered scan is fine at demo volume."""
    if not phone:
        return None
    from boto3.dynamodb.conditions import Attr

    resp = get_table(settings.mock_contacts_table).scan(FilterExpression=Attr("phone").eq(phone))
    items = resp.get("Items", [])
    if not items:
        return None
    items.sort(key=lambda i: not i.get("isPrimary", False))
    return _prefer(items, lambda contact: _active_case(contact) is not None)


def _existing_case(case_id: str) -> "case_store.CaseMetadata":
    try:
        return case_store.get_case_metadata(case_id)
    except case_store.CaseNotFound as exc:
        raise Drop(f"postback for unknown case {case_id}") from exc


def _active_case(contact: dict) -> "case_store.CaseMetadata | None":
    """Newest non-terminal case we last messaged this contact about; else the customer's newest open case."""
    from boto3.dynamodb.conditions import Attr

    contact_id = contact["contactId"]
    sent = _scan(
        get_table(settings.conversations_table),
        FilterExpression=Attr("contactId").eq(contact_id) & Attr("direction").eq("OUTBOUND"),
    )
    for item in sorted(sent, key=lambda i: i.get("sentAt", ""), reverse=True):
        meta = _try_case(item.get("caseId"))
        if meta and meta.state not in TERMINAL_STATES:
            return meta

    customer_id = contact.get("customerId")
    if not customer_id:
        return None
    metas = _scan(
        get_table(settings.cases_table),
        FilterExpression=Attr("sk").eq("META") & Attr("customerId").eq(customer_id),
    )
    open_cases = [m for m in metas if m.get("state") not in TERMINAL_STATES]
    if not open_cases:
        return None
    newest = max(open_cases, key=lambda m: m.get("createdAt", ""))
    return _try_case(newest["caseId"])


def _try_case(case_id: str | None) -> "case_store.CaseMetadata | None":
    if not case_id:
        return None
    try:
        return case_store.get_case_metadata(case_id)
    except case_store.CaseNotFound:
        return None


def _scan(table, **kwargs: Any) -> list[dict]:
    items: list[dict] = []
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


# --- Guardrails ------------------------------------------------------------------------


def _blocked_filter(assessments: Any) -> str | None:
    """Type of the first policy entry whose action is BLOCKED (e.g. PROMPT_ATTACK), if any."""
    if isinstance(assessments, dict):
        if assessments.get("action") == "BLOCKED":
            return str(assessments.get("type") or assessments.get("name") or "BLOCKED")
        for value in assessments.values():
            found = _blocked_filter(value)
            if found:
                return found
    elif isinstance(assessments, list):
        for value in assessments:
            found = _blocked_filter(value)
            if found:
                return found
    return None


def _apply_guardrail(text: str) -> tuple[str, dict]:
    """Run Bedrock Guardrails (INPUT) on customer free text.

    Blocked -> text replaced by a marker. Anonymised (PII) -> guardrail's masked text. Errors propagate
    so the record is retried and eventually parked in the DLQ (fail closed).
    """
    resp = _bedrock().apply_guardrail(
        guardrailIdentifier=GUARDRAIL_ID,
        guardrailVersion=GUARDRAIL_VERSION,
        source="INPUT",
        content=[{"text": {"text": text}}],
    )
    if resp.get("action") != "GUARDRAIL_INTERVENED":
        return text, {"action": "NONE"}
    blocked = _blocked_filter(resp.get("assessments", []))
    if blocked:
        return REDACTED_TEXT, {"action": "GUARDRAIL_INTERVENED", "filter": blocked}
    outputs = resp.get("outputs") or []
    masked = outputs[0].get("text") if outputs else None
    return (masked or REDACTED_TEXT), {"action": "GUARDRAIL_INTERVENED", "filter": "SENSITIVE_INFORMATION"}


# --- Publish ---------------------------------------------------------------------------


def _publish(entry: dict) -> None:
    """PutEvents on the dev bus. On failure, give a consumed postback nonce back so the retry can succeed."""
    entry_detail = entry["detail"]
    envelope = build_envelope(entry.get("source", EventSource.CHANNEL), entry["detailType"], entry_detail)
    envelope["EventBusName"] = settings.bus_name  # build_envelope hardcodes the wrong bus name
    envelope["Detail"] = json.dumps(entry_detail)
    event_id = entry_detail["eventId"]
    nonce_key = event_id[len("evt-pb-"):] if event_id.startswith("evt-pb-") else None  # postback events only
    try:
        resp = _events().put_events(Entries=[envelope])
        if resp.get("FailedEntryCount"):
            raise RuntimeError(f"PutEvents failed: {resp.get('Entries')}")
    except Exception:
        if nonce_key:
            _release_nonce(nonce_key)
        raise


# --- WhatsApp --------------------------------------------------------------------------
# Webhook payload reference: AWS End User Messaging Social "Message and event format" (the SNS message holds the
# AWS header + ``whatsAppWebhookEntry``, a JSON string with Meta's webhook entry: changes[].value.messages/statuses).

WA_WINDOW_TTL_S = 2 * 86400
WA_STATUS_RANK = {"sent": 1, "delivered": 2, "read": 3, "failed": 4}
WA_IGNORED_TYPES = {"reaction", "system", "unsupported", "ephemeral", "request_welcome"}
TECH_INTENTS = {"TECH_ACCEPT", "TECH_DECLINE"}


def _digits(phone: Any) -> str:
    return "".join(ch for ch in str(phone or "") if ch.isdigit())


def wa_user_key(phone: Any) -> str:
    """Same key the Gateway Lambda reads (gateway tools/lambda_handler.py wa_user_key)."""
    return "WAUSER#" + hashlib.sha256(_digits(phone).encode()).hexdigest()[:24]


def _epoch_iso(value: Any) -> str:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return _iso(None)


def _normalize_whatsapp(msg: dict) -> list[dict]:
    raw = msg.get("whatsAppWebhookEntry")
    entry = json.loads(raw) if isinstance(raw, str) else (raw or {})
    header_id = msg.get("messageId")
    out: list[dict] = []
    for change in entry.get("changes", []):
        if change.get("field") != "messages":
            continue
        value = change.get("value") or {}
        for status in value.get("statuses", []):
            _wa_status(status, header_id)
        for message in value.get("messages", []):
            try:
                result = _wa_message(message)
            except Drop as why:
                logger.info("inbound whatsapp dropped: %s", why)
                continue
            if result is not None:
                out.append(result)
    return out


def _wa_message(m: dict) -> dict | None:
    sender = m.get("from") or ""
    provider_id = m.get("id") or ""
    received_at = _epoch_iso(m.get("timestamp"))
    mtype = str(m.get("type") or "")
    _touch_window(sender, received_at)  # any inbound message opens the 24h window
    if mtype in WA_IGNORED_TYPES:
        raise Drop(f"whatsapp {mtype} message")

    pb_value, label = None, None
    if mtype == "interactive":
        it = m.get("interactive") or {}
        reply = it.get("button_reply") or it.get("list_reply") or {}
        pb_value, label = reply.get("id"), reply.get("title")
    elif mtype == "button":  # quick-reply button on a template
        pb_value, label = (m.get("button") or {}).get("payload"), (m.get("button") or {}).get("text")

    technician = _technician_by_phone(sender)

    if pb_value:
        pb = _verify_postback(pb_value, provider_id, sender)
        if pb["intent"] in TECH_INTENTS:
            return _wa_technician_action(pb, technician, sender, provider_id, received_at, label)
        meta = _existing_case(pb["caseId"])
        contact = _contact_by_digits(sender)
        payload = _wa_payload(contact, provider_id, received_at)
        payload.update(caseId=pb["caseId"], kind="POSTBACK", intent=pb["intent"], optionId=pb["optionId"], text=label)
        customer_id = (contact or {}).get("customerId") or meta.customer_id
        _wa_inbound_record(pb["caseId"], provider_id, received_at, label or pb["intent"],
                           senderType="CUSTOMER", contactId=payload["contactId"], intent=pb["intent"])
        return _wa_entry(DetailType.CUSTOMER_ACTION_SELECTED, f"pb-{pb['nonce']}", payload, customer_id,
                         {"type": "CUSTOMER", "id": payload["contactId"] or "UNKNOWN"}, received_at)

    text = ((m.get("text") or {}).get("body") or "").strip() if mtype == "text" else ""

    if technician is not None:
        case_id = _latest_case_for_technician(technician.get("technicianId"))
        _wa_inbound_record(case_id, provider_id, received_at, text or f"[{mtype}]",
                           senderType="TECHNICIAN", technicianId=technician.get("technicianId"))
        logger.info("inbound whatsapp text from technician %s recorded (no agent event)", technician.get("technicianId"))
        return None

    contact = _contact_by_digits(sender)
    if contact is None:
        raise Drop(f"unknown whatsapp sender {mask_phone(sender)}")
    payload = _wa_payload(contact, provider_id, received_at)
    if not text:
        payload["kind"] = "MEDIA"  # image / document / audio / location ...: nothing to scan
    elif text.upper() in OPT_OUT_KEYWORDS:
        payload.update(kind="OPT_OUT", text=text)
    else:
        payload["text"], payload["guardrail"] = _apply_guardrail(text)
    meta = _active_case(contact)
    payload["caseId"] = meta.case_id if meta else None
    customer_id = contact.get("customerId") or (meta.customer_id if meta else None)
    if payload["caseId"]:
        _wa_inbound_record(payload["caseId"], provider_id, received_at, payload["text"] or f"[{mtype}]",
                           senderType="CUSTOMER", contactId=payload["contactId"])
    event_key = f"in-{hashlib.sha1(provider_id.encode()).hexdigest()[:16]}"
    return _wa_entry(DetailType.CUSTOMER_MESSAGE_RECEIVED, event_key, payload, customer_id,
                     {"type": "CUSTOMER", "id": payload["contactId"] or "UNKNOWN"}, received_at)


def _wa_technician_action(pb: dict, technician: dict | None, sender: str, provider_id: str,
                          received_at: str, label: str | None) -> dict | None:
    if technician is None:
        raise Drop(f"technician button from unmatched sender {mask_phone(sender)}")
    tech_id = technician.get("technicianId")
    work_order_id = pb["optionId"]
    _wa_inbound_record(pb["caseId"], provider_id, received_at, label or pb["intent"], senderType="TECHNICIAN",
                       technicianId=tech_id, intent=pb["intent"], workOrderId=work_order_id)
    if pb["intent"] == "TECH_ACCEPT":
        logger.info("technician %s accepted %s on case %s (recorded, no agent event)", tech_id, work_order_id, pb["caseId"])
        return None
    payload = {
        "technicianId": tech_id,
        "workOrderId": work_order_id,
        "available": False,
        "reason": "Technician declined the job on WhatsApp",
        "channel": "WHATSAPP",
        "receivedAt": received_at,
        "providerMessageId": provider_id,
    }
    entry = _wa_entry(DetailType.TECHNICIAN_ETA_UPDATED, f"pb-{pb['nonce']}", payload, None,
                      {"type": "TECHNICIAN", "id": tech_id}, received_at, case_id=pb["caseId"])
    entry["source"] = EventSource.WORKFORCE
    return entry


def _wa_payload(contact: dict | None, provider_id: str, received_at: str) -> dict:
    return {
        "channel": "WHATSAPP",
        "contactId": contact["contactId"] if contact else None,
        "caseId": None,
        "kind": "TEXT",
        "intent": None,
        "optionId": None,
        "text": None,
        "receivedAt": received_at,
        "providerMessageId": provider_id,
        "verified": True,
        "guardrail": {"action": "NONE"},
    }


def _wa_entry(detail_type: DetailType, event_key: str, payload: dict, customer_id: str | None,
              actor: dict, received_at: str, case_id: str | None = None) -> dict:
    if customer_id:
        payload["customerId"] = customer_id
    event_id = f"evt-{event_key}"
    detail = {
        "eventId": event_id,
        "eventVersion": "1.0",
        "occurredAt": received_at,
        "correlationId": event_id,
        "actor": actor,
        "payload": payload,
    }
    case_id = case_id or payload.get("caseId")
    if case_id:
        detail["caseId"] = case_id
    logger.info("inbound whatsapp %s %s case=%s intent=%s", detail_type.value, payload.get("kind", "-"),
                case_id, payload.get("intent"))
    return {"detailType": detail_type, "detail": detail}


def _latest_case_for_technician(technician_id: str | None) -> str | None:
    """caseId of the newest outbound message sent to this technician."""
    from boto3.dynamodb.conditions import Attr

    if not technician_id:
        return None
    items = _scan(get_table(settings.conversations_table),
                  FilterExpression=Attr("technicianId").eq(technician_id) & Attr("direction").eq("OUTBOUND"),
                  ProjectionExpression="caseId, sentAt")
    items = [i for i in items if i.get("caseId")]
    return max(items, key=lambda i: i.get("sentAt", ""))["caseId"] if items else None


def _contact_by_digits(phone: str) -> dict | None:
    """WhatsApp sends digits only (no '+'); mock phones may hold spaces. Compare digits."""
    digits = _digits(phone)
    if not digits:
        return None
    items = [i for i in _scan(get_table(settings.mock_contacts_table)) if _digits(i.get("phone")) == digits]
    if not items:
        return None
    items.sort(key=lambda i: not i.get("isPrimary", False))
    return _prefer(items, lambda contact: _active_case(contact) is not None)


def _technician_by_phone(phone: str) -> dict | None:
    digits = _digits(phone)
    if not digits:
        return None
    items = _scan(get_table(settings.mock_technicians_table), ProjectionExpression="technicianId, phone, tenantId")
    matches = [item for item in items if _digits(item.get("phone")) == digits]
    if not matches:
        return None
    chosen = _prefer(matches, lambda tech: _open_case_of(_latest_case_for_technician(tech.get("technicianId"))))
    return {"technicianId": chosen.get("technicianId")}


def _open_case_of(case_id: str | None) -> bool:
    meta = _try_case(case_id)
    return meta is not None and meta.state not in TERMINAL_STATES


def _touch_window(sender: str, received_at: str) -> None:
    if not _digits(sender):
        return
    get_table(settings.conversations_table).put_item(Item={
        "pk": wa_user_key(sender),
        "sk": "WINDOW",
        "lastInboundAt": received_at,
        "ttl": int(datetime.now(timezone.utc).timestamp()) + WA_WINDOW_TTL_S,
    })


def _wa_inbound_record(case_id: str | None, provider_id: str, received_at: str, body: str, **extra: Any) -> None:
    """Inbound conversation record (pk CASE#<id>); skipped when the message can't be tied to a case."""
    if not case_id:
        return
    item = {
        "pk": f"CASE#{case_id}",
        "sk": f"MSG#{received_at}#{provider_id[-24:] or 'in'}",
        "messageId": provider_id,
        "caseId": case_id,
        "direction": "INBOUND",
        "channel": "WHATSAPP",
        "body": body or "",
        "sentAt": received_at,
    }
    item.update({k: v for k, v in extra.items() if v is not None})
    get_table(settings.conversations_table).put_item(Item=item)


def _wa_status(status: dict, header_id: str | None) -> None:
    """Attach a delivery status (sent / delivered / read / failed) to the outbound message record."""
    from botocore.exceptions import ClientError

    state = str(status.get("status") or "").lower()
    rank = WA_STATUS_RANK.get(state)
    if not rank:
        return
    table = get_table(settings.conversations_table)
    pointer = None
    for key in filter(None, {status.get("id"), header_id}):
        pointer = table.get_item(Key={"pk": f"WAMSG#{key}", "sk": "META"}).get("Item")
        if pointer:
            break
    if not pointer:
        logger.info("whatsapp status %s for an unknown message", state)
        return
    names = {"#ds": "deliveryStatus", "#dr": "deliveryRank", "#dt": "deliveryStatusAt"}
    values: dict[str, Any] = {":s": state.upper(), ":r": rank, ":t": _epoch_iso(status.get("timestamp"))}
    update = "SET #ds = :s, #dr = :r, #dt = :t"
    errors = status.get("errors") or []
    if state == "failed" and errors:
        names["#de"] = "deliveryError"
        values[":e"] = {"code": str(errors[0].get("code", "")), "title": str(errors[0].get("title", ""))[:200]}
        update += ", #de = :e"
        logger.warning("whatsapp message on case %s failed: %s %s", pointer.get("caseId"),
                       errors[0].get("code"), errors[0].get("title"))
    try:
        table.update_item(
            Key={"pk": pointer["msgPk"], "sk": pointer["msgSk"]},
            UpdateExpression=update,
            ConditionExpression="attribute_exists(pk) AND (attribute_not_exists(#dr) OR #dr < :r)",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
