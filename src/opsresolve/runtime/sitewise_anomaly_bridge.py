"""Bridge Lambda: routes AWS IoT SiteWise anomaly-detection results onto the
opsresolve EventBridge bus (OPS-004 assist).

IoT Rules Engine has no native EventBridge action (confirmed against the full list
of 21 supported rule-action types), so this small Lambda is the "Anomaly Lambda
(IoT rule)" producer the tech spec assumes for the tcs.opsresolve.telemetry /
"Telemetry Anomaly Detected" shape (Section 8.1) -- feeding the *same* detail-type
the existing ``opsresolve_dev_telemetry_breach`` threshold rule already produces.

Both producers are intentional and run side by side, not one replacing the other
(tasks.md OPS-004 assist decision): the deterministic threshold rule stays as a
reliability net regardless of how well-trained the SiteWise model is. Because both
feed the same (source, detail-type) pair, ``runtime.normalize.ROUTING_TABLE`` and
``common/events.py`` need no changes -- they already treat this detail-type as one
TELEMETRY trigger regardless of which producer fired it. ``payload.detectionMethod``
(THRESHOLD vs SITEWISE_ML, set below) is how the case timeline tells them apart.

Trigger: an IoT rule subscribed to the SiteWise property-value-update MQTT topic for
the ``anomalyResult`` property, invoking this Lambda with the raw ``PropertyValueUpdate``
message as its event (AWS IoT SiteWise docs, "Query asset property notifications")::

    {"type": "PropertyValueUpdate",
     "payload": {"assetId": ..., "propertyId": ...,
                 "values": [{"timestamp": {...}, "quality": ..., "value": {"stringValue": ...}}]}}

The ``stringValue`` itself is a second layer of JSON -- SiteWise's own inference
result shape (AWS docs, "Review inference results")::

    {"timestamp": ..., "prediction": 0|1, "prediction_reason": "...",
     "anomaly_score": 0.xx, "diagnostics": [...]}

Only ``prediction == 1`` (ANOMALY_DETECTED) produces a bus event -- a normal reading
isn't something the orchestrator needs to react to, and the model likely runs
inference on a fixed schedule, so publishing on every tick would flood the bus for
no benefit.

Not yet wired to any deployed IoT rule -- see resource.md OPS-004 assist section and
tasks.md's Next Up list. Deploying this (function + role + IoT rule + notification
enable) is an AWS mutation, done only on an explicit go.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from ..common.config import settings
from ..common.events import DetailType, EventSource
from ..common.logging import get_logger

logger = get_logger(__name__)

# SiteWise's own internal asset UUID -> the business assetId the rest of the
# platform (case store, tools, orchestrator) uses. Static because only one
# SiteWise asset exists today (see resource.md OPS-004 assist section); extend
# this map, or replace with a DescribeAsset lookup, when more assets are onboarded.
SITEWISE_ASSET_ID_MAP: dict[str, str] = {
    "b9a97cc7-4e95-45a1-b09f-5bf28d77d3dc": "CHILLER-8821",
}

PREDICTION_ANOMALY_DETECTED = 1


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any] | None:
    """IoT rule Lambda-action entry point. One ``PropertyValueUpdate`` message per
    invocation. Filtering on ``prediction == 1`` happens here rather than in the IoT
    rule's SQL, since that requires parsing the nested ``stringValue`` JSON, which IoT
    SQL can't reach into.
    """
    if event.get("type") != "PropertyValueUpdate":
        logger.info(f"Ignoring non-PropertyValueUpdate message: type={event.get('type')!r}")
        return None

    payload = event.get("payload", {})
    sitewise_asset_id = payload.get("assetId")
    values = payload.get("values") or []
    if not values:
        logger.info("PropertyValueUpdate with no values; nothing to do.")
        return None

    # A notification batch can carry more than one TQV; only the most recent
    # inference result matters here.
    latest = values[-1]
    string_value = (latest.get("value") or {}).get("stringValue")
    if not string_value:
        logger.error(f"anomalyResult value has no stringValue: {latest!r}")
        return None

    try:
        inference = json.loads(string_value)
    except (TypeError, ValueError) as exc:
        logger.error(f"anomalyResult stringValue is not valid JSON: {exc}")
        return None

    prediction = inference.get("prediction")
    if prediction != PREDICTION_ANOMALY_DETECTED:
        logger.info(
            f"No anomaly (prediction={prediction!r}, "
            f"reason={inference.get('prediction_reason')!r}); not publishing."
        )
        return None

    asset_id = SITEWISE_ASSET_ID_MAP.get(sitewise_asset_id, sitewise_asset_id)
    return _publish_anomaly_event(asset_id, inference, latest)


def _publish_anomaly_event(
    asset_id: str, inference: dict[str, Any], tqv: dict[str, Any]
) -> dict[str, Any]:
    event_id = f"evt-sitewise-{uuid.uuid4().hex[:12]}"
    correlation_id = event_id
    occurred_at = _tqv_timestamp_iso(tqv) or datetime.now(timezone.utc).isoformat()

    detail = {
        "eventId": event_id,
        "eventVersion": "1.0",
        "occurredAt": occurred_at,
        "caseId": None,
        "correlationId": correlation_id,
        "actor": {"type": "SYSTEM", "channel": "IOT", "id": "sitewise-anomaly-bridge"},
        "payload": {
            "assetId": asset_id,
            "detectionMethod": "SITEWISE_ML",
            "predictionReason": inference.get("prediction_reason"),
            "anomalyScore": inference.get("anomaly_score"),
            "diagnostics": inference.get("diagnostics"),
        },
    }

    # common.events.build_envelope() hardcodes EventBusName="opsresolve-bus", which
    # doesn't match the deployed bus opsresolve-dev-bus (settings.prefix + "-bus") --
    # a pre-existing inconsistency, flagged in resource.md rather than fixed here
    # since it's shared code outside this task's scope. Building the entry directly
    # so this module actually targets the real bus.
    entry = {
        "Source": EventSource.TELEMETRY.value,
        "DetailType": DetailType.TELEMETRY_ANOMALY_DETECTED.value,
        "EventBusName": f"{settings.prefix}-bus",
        "Detail": json.dumps(detail),
    }

    response = _events_client().put_events(Entries=[entry])
    if response.get("FailedEntryCount", 0):
        logger.error(f"PutEvents failed for {event_id}: {response['Entries']}")
        raise RuntimeError(f"PutEvents failed: {response['Entries']}")

    logger.info(
        f"Published Telemetry Anomaly Detected (SITEWISE_ML) for assetId={asset_id}, "
        f"eventId={event_id}, anomalyScore={inference.get('anomaly_score')}"
    )
    return {"eventId": event_id, "assetId": asset_id, "published": True}


def _tqv_timestamp_iso(tqv: dict[str, Any]) -> str | None:
    ts = tqv.get("timestamp") or {}
    seconds = ts.get("timeInSeconds")
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def _events_client():  # pragma: no cover - thin AWS SDK wrapper
    import boto3

    return boto3.client("events", region_name=settings.region)
