"""Field app Knowledge Assistant Lambda: POST /mobile-web-app/assistant/messages.

Handler: opsresolve.field_api.field_assistant.handler

Stateless (nothing stored): KB Retrieve (no LLM) on ``TA0KF90K4I`` + Bedrock Converse with
Nova 2 Lite. See API_requirements.md Section 11 for the full behavioural contract.
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from ..common.config import settings
from ..common.logging import get_logger
from . import _common as c

logger = get_logger(__name__)

KNOWLEDGE_BASE_ID = "TA0KF90K4I"
NUM_RESULTS = 5
MAX_HISTORY_TURNS = 6
MAX_OUTPUT_TOKENS = 1200

_SYSTEM_PROMPT = (
    "You are a field-technician assistant for OpsResolve. Answer only from the retrieved "
    "context provided below. Give numbered, step-by-step instructions. Always mention safety "
    "precautions first when relevant. If the context does not contain the answer, say you are "
    "not sure rather than guessing. Never use knowledge from outside the retrieved context, and never answer about "
    "equipment the retrieved context does not cover. Give answer within 300 words."
)


_NO_CONTEXT_ANSWER = "I could not find this in your company's knowledge base, so I am not sure. Please check with your supervisor."


class _HttpError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _bad(message: str) -> _HttpError:
    return _HttpError(400, "BAD_REQUEST", message)


@lru_cache(maxsize=1)
def _ddb():  # pragma: no cover - thin SDK wrapper
    import boto3

    return boto3.resource("dynamodb", region_name=settings.region)


@lru_cache(maxsize=1)
def _bedrock_agent_runtime():  # pragma: no cover - thin SDK wrapper
    import boto3

    return boto3.client("bedrock-agent-runtime", region_name=settings.region)


@lru_cache(maxsize=1)
def _bedrock_runtime():  # pragma: no cover - thin SDK wrapper
    import boto3

    return boto3.client("bedrock-runtime", region_name=settings.region)


@lru_cache(maxsize=1)
def _s3():  # pragma: no cover - thin SDK wrapper
    import boto3

    return boto3.client("s3", region_name=settings.region)


def _model_id() -> str:
    """Nova 2 Lite model id from config/ssm_parameters.json's "lightweight" model entry."""
    import os

    return os.getenv("OPSRESOLVE_NOVA_LITE_MODEL_ID", "us.amazon.nova-2-lite-v1:0")


def _asset_context(job_id: str | None, tenant_id: str | None = None) -> dict | None:
    if not job_id:
        return None
    from boto3.dynamodb.conditions import Key

    resp = _ddb().Table(settings.cases_table).query(KeyConditionExpression=Key("pk").eq(f"CASE#{job_id}"))
    items = resp.get("Items", [])
    meta = next((i for i in items if i.get("sk") == "META"), None)
    if meta is None or (tenant_id and (meta.get("tenantId") or "meravix-foods") != tenant_id):
        return None
    asset = None
    if meta.get("assetId"):
        asset = _ddb().Table(settings.mock_assets_table).get_item(
            Key={"pk": f"ASSETS#{meta['assetId']}", "sk": "META"}
        ).get("Item")
    return {
        "model": (asset or {}).get("model"),
        "firmware": (asset or {}).get("firmware"),
        "requiredPart": meta.get("requiredPart"),
        "customerDescription": meta.get("customerDescription"),
    }


# The KB is a managed one: Retrieve rejects ``vectorSearchConfiguration`` (it made every question answer without
# context) and the SDK has no metadata filter for it, so the technician's company data source is picked from the results.
_KB_SOURCE_BY_TENANT = {"meravix-foods": "YWUONIAPVO", "meravix-broadband": "DBUG8GGNTX"}


def _retrieve(query_text: str, tenant_id: str | None = None) -> list[dict]:
    resp = _bedrock_agent_runtime().retrieve(knowledgeBaseId=KNOWLEDGE_BASE_ID, retrievalQuery={"text": query_text})
    source = _KB_SOURCE_BY_TENANT.get(tenant_id or "meravix-foods")
    results = resp.get("retrievalResults", [])
    if source:
        results = [r for r in results if (r.get("metadata") or {}).get("_data_source_id") == source]
    return results[:NUM_RESULTS]


def _citation_title(source_uri: str) -> str:
    name = source_uri.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[0].replace("_", " ")


def _image_content_block(image_key: str) -> dict | None:
    try:
        obj = _s3().get_object(Bucket=f"{settings.prefix}-field-uploads", Key=image_key)
        data = obj["Body"].read()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"failed to fetch assistant image {image_key}: {exc}")
        return None
    content_type = obj.get("ContentType", "image/jpeg")
    fmt = content_type.split("/")[-1]
    return {"image": {"format": fmt, "source": {"bytes": data}}}


def _ask(event: dict, technician_id: str) -> dict:
    body = c.parse_json_body(event)
    if body is None:
        raise _bad("Body must be a JSON object")

    job_id = body.get("jobId")
    text = body.get("text")
    image_key = body.get("imageKey")
    history = body.get("history") or []

    if not text and not image_key:
        raise _bad("text or imageKey is required")
    if not isinstance(history, list):
        raise _bad("history must be a list")
    history = history[-MAX_HISTORY_TURNS:]

    tenant_id = c.caller_tenant(event)
    context = _asset_context(job_id, tenant_id)
    query_text = text or "image question"
    if context and context.get("model"):
        query_text = f"{query_text} {context['model']}"

    try:
        chunks = _retrieve(query_text, tenant_id)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"KB retrieve failed: {exc}")
        chunks = []

    retrieved_text = "\n\n".join(
        c_.get("content", {}).get("text", "") for c_ in chunks if c_.get("content", {}).get("text")
    )
    citations = []
    for c_ in chunks:
        uri = (c_.get("location", {}).get("s3Location") or {}).get("uri", "")
        citations.append({
            "title": _citation_title(uri) if uri else "Knowledge base",
            "source": uri,
            "excerpt": (c_.get("content", {}).get("text") or "")[:300],
            "score": c_.get("score"),
        })

    if not retrieved_text:
        # Nothing of this company's knowledge matched (e.g. an S1 CX-500 question from an S2 technician): do not let
        # the model answer from its own knowledge.
        return {"answer": _NO_CONTEXT_ANSWER, "citations": [], "modelId": _model_id(),
                "usage": {"inputTokens": 0, "outputTokens": 0}}

    messages = []
    for turn in history:
        role = turn.get("role")
        if role in ("user", "assistant") and turn.get("text"):
            messages.append({"role": role, "content": [{"text": turn["text"]}]})

    user_content: list[dict] = []
    if retrieved_text:
        user_content.append({"text": f"Retrieved context:\n{retrieved_text}"})
    if context and context.get("customerDescription"):
        user_content.append({"text": f"Job context: {context['customerDescription']}"})
    if text:
        user_content.append({"text": text})
    if image_key:
        image_block = _image_content_block(image_key)
        if image_block:
            user_content.append(image_block)
    if not user_content:
        user_content.append({"text": text or ""})
    messages.append({"role": "user", "content": user_content})

    resp = _bedrock_runtime().converse(
        modelId=_model_id(),
        system=[{"text": _SYSTEM_PROMPT}],
        messages=messages,
        inferenceConfig={"maxTokens": MAX_OUTPUT_TOKENS},
    )

    output_message = resp.get("output", {}).get("message", {})
    answer = "".join(
        block.get("text", "") for block in output_message.get("content", []) if "text" in block
    )
    usage = resp.get("usage", {})

    return {
        "answer": answer,
        "citations": citations,
        "modelId": _model_id(),
        "usage": {"inputTokens": usage.get("inputTokens"), "outputTokens": usage.get("outputTokens")},
    }


def handler(event: dict, context: Any = None) -> dict:
    route_key = event.get("routeKey", "")
    if route_key != "POST /mobile-web-app/assistant/messages":
        return c.error(404, "NOT_FOUND", f"Unknown route {route_key}")

    try:
        technician_id, _username = c.technician_identity(event)
    except c.AuthError as exc:
        return c.error(exc.status, exc.code, exc.message)

    try:
        body = _ask(event, technician_id)
    except _HttpError as exc:
        return c.error(exc.status, exc.code, exc.message)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"field_assistant failed: {exc}")
        return c.error(500, "INTERNAL_ERROR", "Internal error")
    return c.ok(body)
