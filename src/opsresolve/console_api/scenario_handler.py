"""T6: Scenario Controller backend for the Ops Console.

Handler: opsresolve.console_api.scenario_handler.handler

  GET  /scenario/telemetry/profiles    read-only: the profiles POST /scenario/telemetry accepts (UI dropdown)
  POST /scenario/reset                 wipe the CALLER'S TENANT's cases/approvals/conversations, re-seed its baseline
  POST /scenario/telemetry             {profile, assetId?} -> IoT publish opsresolve/telemetry/<assetId>. The
                                       "degrading" profile is streamed (one reading / 10 s for ~7 min) by an async
                                       run of this Lambda after SiteWise inference is started; the worker stops it.
  POST /scenario/technician-delay      {caseId?} -> ``Technician ETA Updated`` on the bus
  POST /scenario/part-shortage         {caseId?} -> ``Part Pick Failed`` on the bus
  POST /scenario/complete-work-order   {caseId?} -> ``Work Order Completed`` on the bus
  POST /scenario/customer-message      {text?} -> ``Customer Message Received`` from the tenant's demo contact

Every route is scoped to the caller's tenant (JWT claim ``custom:tenantId``; a missing claim is a 403) and a
button the tenant does not list in ``scenarioButtons`` answers 400 NOT_AVAILABLE. A case/row with no tenant
belongs to ``meravix-foods``.

Every response body is ``{"ok": bool, "logs": [{"ts","level","message"}], ...}`` (on failure an
``error: {code, message}`` is added).

Reset, after the wipe + seed, also (each step on its own; an SDK error becomes a ``warnings``
entry, never a 500), always limited to the caller's tenant: frees its BOOKED windows in field-availability
(status update only; S2 also recreates its technicians' seeded windows), SETs ``onHand`` on the
mock-inventory rows in ``INVENTORY_BY_TENANT`` (no other attribute, no new rows), empties its rows in the
Gateway demo tables (reservations, transfers, appointments, remote-actions), deletes its mock-work-order-history
rows whose id is not an original one, deletes its cached tool results from the idempotency table (``IDEMP#...``
and Gateway rows whose sk is a tool name; ``NONCE#``/``LOCK#`` rows are kept), deletes the pending case timers of
its cases and records it as the active demo tenant. A row belongs to a tenant when it names one of that tenant's
case ids; a row that names no case belongs to ``meravix-foods``. Other mock-* tables and telemetry are never
touched.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..common import tenants
from ..common.logging import get_logger
from ..runtime.normalize import normalize_event
from . import _common as c
from .scenario_seed import DEFAULT_TENANT, INVENTORY_BY_TENANT, ORIGINAL_WORK_ORDER_IDS_BY_TENANT, build_baseline

logger = get_logger(__name__)

# Tables wiped by reset (only the caller's tenant's rows): key attribute names (resource.md) and the row's case id.
_RESET_TABLES: dict[str, tuple[tuple[str, ...], Any]] = {
    c.CASES_TABLE: (("pk", "sk"), lambda r: str(r.get("pk", "")).removeprefix("CASE#")),
    c.APPROVALS_TABLE: ((c.APPROVAL_PK,), lambda r: r.get("caseId")),
    c.CONVERSATIONS_TABLE: (("pk", "sk"), lambda r: str(r.get("pk", "")).removeprefix("CASE#")),
}

# Reset extras (C4/C8/C9). Technician windows (mobile app): pk TECHNICIAN#<id>, sk SLOT#<date>#<slotId>.
AVAILABILITY_TABLE = f"{c.settings.prefix}-field-availability"
SEEDED_SLOT_PREFIX = "seed-"  # slotId of the windows Reset creates for seeded technician jobs
# Gateway demo tables (Lakshmi's OPS-007, resource.md), hash key only; Reset empties them.
GATEWAY_DEMO_TABLES: dict[str, str] = {
    f"{c.settings.prefix}-reservations": "reservationId",
    f"{c.settings.prefix}-transfers": "transferId",
    f"{c.settings.prefix}-appointments": "workOrderId",
    f"{c.settings.prefix}-remote-actions": "actionId",
}
# Same names as agents.gateway_client.GATEWAY_TOOL_NAMES (copied: this Lambda does not ship
# the agents package). The Gateway caches a tool result as pk = idempotencyKey, sk = tool name.
GATEWAY_TOOL_NAMES: frozenset[str] = frozenset({
    "executeSafeRemoteAction", "generateServiceReport", "closeServiceCase", "retrieveTroubleshootingKnowledge",
    "findAndReservePart", "releasePartReservation", "transferPart", "proposeAppointmentSlots",
    "createServiceAppointment", "reassignTechnician", "updateWorkOrder", "sendCustomerMessage", "requestApproval",
    "applyServiceCredit", "dispatchCourier",
})

DEFAULT_ASSET_ID = "CHILLER-8821"
DEFAULT_CUSTOMER_TEXT = "My internet keeps dropping"
# Buttons a tenant may enable in META ``scenarioButtons`` (Reset is always available).
BUTTON_ACTIONS = frozenset({"telemetry", "technician-delay", "part-shortage", "complete-work-order",
                            "customer-message"})
_CHANNEL_SOURCE = "tcs.opsresolve.channel"  # EventSource.CHANNEL (copied: the Lambda zip does not ship events.py)
GUARDRAIL_ID = os.environ.get("GUARDRAIL_ID", "xa0zjafyaz7z")  # same guardrail as the inbound normalizer
GUARDRAIL_VERSION = os.environ.get("GUARDRAIL_VERSION", "DRAFT")
REDACTED_TEXT = "[redacted by guardrail]"
_TENANT = ""  # caller's tenant for the current invocation (set in handler)

# IoT SiteWise anomaly detection for CHILLER-8821 (resource.md "OPS-004 assist"). Inference is not left running:
# POST /scenario/telemetry starts it for a streamed profile and the stream worker stops it afterwards.
SITEWISE_ASSET_ID = "b9a97cc7-4e95-45a1-b09f-5bf28d77d3dc"
SITEWISE_MODEL_ID = "54c2c80f-48f5-435b-9d42-2d63b9b162a4"
SITEWISE_INFERENCE_ACTION_ID = "3ec7ca3c-81c3-497b-ba4e-d8e7e3eb25c7"  # AWS/ANOMALY_DETECTION_INFERENCE
INFERENCE_START_PAYLOAD = json.dumps({"inferenceMode": "START", "dataUploadFrequency": "PT5M"}, separators=(",", ":"))
INFERENCE_STOP_PAYLOAD = json.dumps({"inferenceMode": "STOP"}, separators=(",", ":"))

# Streamed profiles: one reading every STREAM_INTERVAL_SEC (the model was trained at PT10S) through the real
# IoT topic, from a second asynchronous run of this Lambda (the API call itself must return within 30 s).
STREAM_INTERVAL_SEC = 10
INFERENCE_GRACE_SEC = 330  # after the last reading: one more PT5M inference cycle plus margin, then STOP
WORKER_SAFETY_SEC = 40  # time kept free before the Lambda timeout
SCENARIO_FUNCTION_NAME = os.environ.get("AWS_LAMBDA_FUNCTION_NAME") or f"{c.settings.prefix}-console-scenario"
STREAM_WORKER_KEY = "scenarioWorker"  # direct (async) invocation only; API Gateway events never carry it


def _degrading_series() -> list[dict[str, Any]]:
    """18 normal readings (3 min), then 24 with the compressor motor stalled (4 min).

    Normal values match the SiteWise training data (supplyTempC 5.9, compressorPressureVar 0.056, motorCurrentA
    37.1); the motorCurrentA stall to 1.5-2.5 A is the pattern the model was verified to flag (resource.md,
    "Inference verified with synthetic data"). Pressure variance and supply temperature climb during the stall.
    """
    temp_n, pv_n, amp_n = (5.88, 5.90, 5.92, 5.90), (0.055, 0.056, 0.057, 0.056), (37.0, 37.1, 37.2, 37.1)
    stall_amps = (2.0, 1.8, 2.3, 1.6, 2.1, 2.4)
    normal_n, stall_n = 18, 24
    series: list[dict[str, Any]] = [
        {"supplyTempC": temp_n[i % 4], "compressorPressureVar": pv_n[i % 4], "motorCurrentA": amp_n[i % 4],
         "alarm": None} for i in range(normal_n)]
    for j in range(stall_n):
        pv = round(0.056 + (j + 1) * (0.274 - 0.056) / stall_n, 3)
        series.append({"supplyTempC": round(5.9 + (j + 1) * (7.7 - 5.9) / stall_n, 2),
                       "compressorPressureVar": pv, "motorCurrentA": stall_amps[j % len(stall_amps)],
                       "alarm": "CMP_CTRL_PV" if pv > 0.18 else None})
    return series


# Telemetry profiles (samples from telemetry/profiles/s1_chiller_*.csv; `ts` set at publish time).
TELEMETRY_PROFILES: dict[str, list[dict[str, Any]]] = {
    "normal": [
        {"supplyTempC": 6.0, "compressorPressureVar": 0.06, "motorCurrentA": 38.5, "alarm": None},
    ] * 3,
    "degrading": _degrading_series(),
    "post_reset_abnormal": [
        {"supplyTempC": 7.4, "compressorPressureVar": 0.16, "motorCurrentA": 40.0, "alarm": None},
        {"supplyTempC": 8.25, "compressorPressureVar": 0.262, "motorCurrentA": 41.7, "alarm": "CMP_CTRL_PV"},
    ],
    "repaired": [
        {"supplyTempC": 5.9, "compressorPressureVar": 0.06, "motorCurrentA": 38.2, "alarm": None},
    ] * 3,
}
_PROFILE_ALIASES = {
    "s1_chiller_normal": "normal", "s1_chiller_degrading": "degrading",
    "s1_chiller_post_reset_still_abnormal": "post_reset_abnormal", "s1_chiller_repaired": "repaired",
}

DEFAULT_ASSET_MODEL = "CX-500"
DEFAULT_PROFILE_ID = "degrading"
STREAMED_PROFILES = frozenset({"degrading"})  # published over time by the stream worker, scored by SiteWise

# Dropdown metadata for GET /scenario/telemetry/profiles, in display order. Keys == TELEMETRY_PROFILES.
TELEMETRY_PROFILE_INFO: dict[str, dict[str, Any]] = {
    "degrading": {
        "label": "Predictive failure — CX-500",
        "description": "Streams about 7 minutes of readings: the compressor motor stalls while pressure variance "
                       "and supply temperature climb to alarm CMP_CTRL_PV. IoT SiteWise anomaly detection flags "
                       "it and a case opens about 5 to 10 minutes after you start it.",
        "opensCase": True,
    },
    "post_reset_abnormal": {
        "label": "Still abnormal after remote reset — CX-500",
        "description": "Readings stay above the breach threshold after a remote controller reset, "
                       "so the case moves on to field service.",
        "opensCase": True,
    },
    "repaired": {
        "label": "Repaired / back to normal — CX-500",
        "description": "Readings back in band after the repair; used to verify a fix.",
        "opensCase": False,
    },
    "normal": {
        "label": "Normal operation — CX-500",
        "description": "Baseline readings with no anomaly.",
        "opensCase": False,
    },
}


class _Fail(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class _Log:
    def __init__(self) -> None:
        self.lines: list[dict[str, str]] = []

    def add(self, message: str, level: str = "INFO") -> None:
        self.lines.append({"ts": _now(), "level": level, "message": message})
        logger.info(message)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@lru_cache(maxsize=1)
def _ddb():  # pragma: no cover - thin SDK wrapper
    import boto3
    return boto3.resource("dynamodb", region_name=c.settings.region)


@lru_cache(maxsize=1)
def _events():  # pragma: no cover - thin SDK wrapper
    import boto3
    return boto3.client("events", region_name=c.settings.region)


@lru_cache(maxsize=1)
def _iot():  # pragma: no cover - thin SDK wrapper
    import boto3
    return boto3.client("iot-data", region_name=c.settings.region)


@lru_cache(maxsize=1)
def _sitewise():  # pragma: no cover - thin SDK wrapper
    import boto3
    return boto3.client("iotsitewise", region_name=c.settings.region)


@lru_cache(maxsize=1)
def _bedrock():  # pragma: no cover - thin SDK wrapper
    import boto3
    return boto3.client("bedrock-runtime", region_name=c.settings.region)


@lru_cache(maxsize=1)
def _lambda():  # pragma: no cover - thin SDK wrapper
    import boto3
    return boto3.client("lambda", region_name=c.settings.region)


def _sleep(seconds: float) -> None:  # separate name so tests can skip the waits
    time.sleep(seconds)


def _scan_all(table, **kwargs: Any) -> list[dict]:
    items: list[dict] = []
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            return items
        kwargs["ExclusiveStartKey"] = lek


# --- reset -------------------------------------------------------------------------------------

def _case_tenants() -> dict[str, str]:
    """caseId -> tenant for every case in the store (a case without a tenantId is meravix-foods)."""
    from boto3.dynamodb.conditions import Attr
    rows = _scan_all(_ddb().Table(c.CASES_TABLE), FilterExpression=Attr("sk").eq("META"),
                     ProjectionExpression="caseId, tenantId")
    return {r["caseId"]: r.get("tenantId") or DEFAULT_TENANT for r in rows if r.get("caseId")}


def _owner_of(row: dict, owners: dict[str, str]) -> str | None:
    """Tenant a row belongs to: its ``caseId`` when known, else the first known case id named in any text value."""
    case_id = row.get("caseId")
    if isinstance(case_id, str) and case_id in owners:
        return owners[case_id]
    for value in row.values():
        if isinstance(value, str):
            for known, tenant in owners.items():
                if known in value:
                    return tenant
    return None


def _is_mine(owner: str | None, tenant: str) -> bool:
    return (owner or DEFAULT_TENANT) == tenant


def _reset(log: _Log) -> dict:
    tenant = _TENANT
    owners = _case_tenants()  # before the wipe: later steps match leftovers of the wiped cases by id
    deleted: dict[str, int] = {}
    for name, (keys, case_of) in _RESET_TABLES.items():
        table = _ddb().Table(name)
        names = {f"#k{i}": k for i, k in enumerate(keys)}
        proj = ", ".join(names)
        if name == c.APPROVALS_TABLE:
            names["#c"], proj = "caseId", proj + ", #c"
        existing = [r for r in _scan_all(table, ProjectionExpression=proj, ExpressionAttributeNames=names)
                    if _is_mine(owners.get(case_of(r)), tenant)]
        with table.batch_writer() as bw:
            for it in existing:
                bw.delete_item(Key={k: it[k] for k in keys})
        deleted[name] = len(existing)
        log.add(f"Cleared {len(existing)} {tenant} items from {name}")
    baseline = build_baseline(tenant_id=tenant)
    for name, items in ((c.CASES_TABLE, baseline["cases"]), (c.APPROVALS_TABLE, baseline["approvals"])):
        with _ddb().Table(name).batch_writer() as bw:
            for it in items:
                bw.put_item(Item=it)
        log.add(f"Seeded {len(items)} items into {name}")
    n_cases = sum(1 for i in baseline["cases"] if i["sk"] == "META")

    # DD10: each extra runs on its own; an SDK error (e.g. AccessDenied before the IAM change,
    # or a missing table) becomes a WARN line + a ``warnings`` entry instead of failing the Reset.
    extras: dict[str, Any] = {"availabilityFreed": 0, "inventoryReset": 0, "timersDeleted": 0,
                              "seededWindowsBooked": 0, "windowsCreated": 0}
    warnings: list[str] = []

    def step(label: str, fn) -> None:
        try:
            fn()
        except (ClientError, BotoCoreError) as exc:
            code = exc.response.get("Error", {}).get("Code", "Error") if isinstance(exc, ClientError) \
                else type(exc).__name__
            msg = f"Skipped {label}: {code}"
            log.add(msg, "WARN")
            warnings.append(msg)

    def free_windows() -> None:
        extras["availabilityFreed"] = _free_booked_windows(log, owners, tenant)

    def reset_inventory() -> None:
        extras["inventoryReset"] = _reset_inventory(log, tenant)

    def book_seeded_windows() -> None:
        extras["seededWindowsBooked"] = _book_seeded_job_windows(log, baseline["cases"])

    def seed_tech_windows() -> None:
        extras["windowsCreated"] = _seed_s2_windows(log)

    def clear_table(name: str, keys: tuple[str, ...], keep=None, full: bool = False) -> None:
        deleted[name] = _clear_table(name, keys, log, keep=keep, full=full)

    def not_mine(row: dict) -> bool:
        return not _is_mine(_owner_of(row, owners), tenant)

    step("freeing booked availability windows", free_windows)
    step("booking the windows of seeded technician jobs", book_seeded_windows)
    if tenant == "meravix-broadband":
        step("creating the technician windows", seed_tech_windows)
    step("resetting part stock", reset_inventory)
    for name, key in GATEWAY_DEMO_TABLES.items():
        step(f"clearing {name}", lambda name=name, key=key: clear_table(name, (key,), keep=not_mine, full=True))
    originals = frozenset().union(*ORIGINAL_WORK_ORDER_IDS_BY_TENANT.values())
    step("clearing demo work orders", lambda: clear_table(
        c.settings.mock_work_order_history_table, ("pk", "sk"), full=True,
        keep=lambda it: it["pk"].partition("#")[2] in originals or not_mine(it)))
    step("clearing cached tool results", lambda: clear_table(
        c.settings.idempotency_table, ("pk", "sk"), full=True,
        keep=lambda it: not _is_cached_tool_result(it) or not_mine(it)))

    def delete_timers() -> None:
        extras["timersDeleted"] = _delete_pending_timers(log, lambda name: _is_mine(_owner_of({"n": name}, owners), tenant))

    step("deleting pending case timers", delete_timers)
    step("recording the active demo tenant", lambda: _set_active_tenant(log, tenant))

    log.add(f"Reset complete ({tenant}): {n_cases} cases, {len(baseline['approvals'])} approvals")
    return {"tenantId": tenant, "deleted": deleted, "cases": n_cases, "approvals": len(baseline["approvals"]),
            **extras, "warnings": warnings}


def _set_active_tenant(log: _Log, tenant: str) -> None:
    tenants.set_active_demo_tenant(tenant)
    log.add(f"Active demo tenant is now {tenant}")


TIMER_NAME_PREFIX = "opsresolve-"  # safety gate: only schedules with this prefix are ever deleted (code AND IAM)


@lru_cache(maxsize=1)
def _scheduler():  # pragma: no cover - thin SDK wrapper
    import boto3
    return boto3.client("scheduler", region_name=c.settings.region)


def _delete_pending_timers(log: _Log, mine=lambda name: True) -> int:
    """Delete the case timers (EventBridge Scheduler, group ``<prefix>-timers``) that have not fired yet and that
    ``mine(name)`` accepts (the caller's tenant's cases), so a timer cannot hit a case after Reset. Only names
    starting with ``opsresolve-`` are touched. A missing group counts as nothing to delete."""
    group = f"{c.settings.prefix}-timers"
    client = _scheduler()
    names: list[str] = []
    try:
        for page in client.get_paginator("list_schedules").paginate(GroupName=group, NamePrefix=TIMER_NAME_PREFIX):
            names += [s["Name"] for s in page.get("Schedules", [])
                      if s["Name"].startswith(TIMER_NAME_PREFIX) and mine(s["Name"])]
        deleted = 0
        for name in names:
            try:
                client.delete_schedule(GroupName=group, Name=name)
                deleted += 1
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":  # fired meanwhile
                    raise
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
            log.add(f"No timer group {group}; nothing to delete")
            return 0
        raise
    log.add(f"Deleted {deleted} pending case timers from {group}")
    return deleted


def _book_seeded_job_windows(log: _Log, case_items: list[dict]) -> int:
    """Mark the availability window of every future SCHEDULED technician job in the baseline as BOOKED
    (the seeded filler job SR-FIELD-TECH204-01 / WO-88201), so the agent never offers a slot that clashes
    with it. A window the technician already added in the mobile app (same date, start, end) is updated;
    otherwise one is created. UpdateItem only (upsert), so no extra IAM action is needed."""
    from datetime import datetime as _dt

    def parse(value: str) -> _dt:
        return _dt.fromisoformat(value.replace("Z", "+00:00"))

    now = _dt.now(timezone.utc)
    table = _ddb().Table(AVAILABILITY_TABLE)
    windows: list[dict] | None = None
    booked = 0
    for it in case_items:
        if (not str(it.get("sk", "")).startswith("APPT#") or it.get("status") != "SCHEDULED"
                or not it.get("technicianId") or not it.get("slotStart") or not it.get("slotEnd")):
            continue
        start, end = parse(it["slotStart"]), parse(it["slotEnd"])
        if start <= now:
            continue
        tech, work_order, case_id = it["technicianId"], str(it["sk"])[5:], str(it["pk"])[5:]
        date, s, e = start.strftime("%Y-%m-%d"), start.strftime("%H:%M"), end.strftime("%H:%M")
        if windows is None:
            windows = _scan_all(table)
        existing = next((w for w in windows if w.get("technicianId") == tech and w.get("date") == date
                         and w.get("start") == s and w.get("end") == e), None)
        slot_id = existing["slotId"] if existing else f"{SEEDED_SLOT_PREFIX}{work_order}"
        key = {"pk": existing["pk"], "sk": existing["sk"]} if existing else \
            {"pk": f"TECHNICIAN#{tech}", "sk": f"SLOT#{date}#{slot_id}"}
        table.update_item(
            Key=key,
            UpdateExpression=("SET #s = :booked, appointmentId = :wo, caseId = :case, bookedAt = :now, "
                              "slotId = :slot, technicianId = :tech, #d = :date, #st = :start, #en = :end, "
                              "createdAt = if_not_exists(createdAt, :now), note = if_not_exists(note, :note)"),
            ExpressionAttributeNames={"#s": "status", "#d": "date", "#st": "start", "#en": "end"},
            ExpressionAttributeValues={":booked": "BOOKED", ":wo": work_order, ":case": case_id,
                                       ":now": _dt.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                       ":slot": slot_id, ":tech": tech, ":date": date, ":start": s, ":end": e,
                                       ":note": f"Scheduled job {work_order}"})
        booked += 1
        log.add(f"Booked {tech} window {date} {s}-{e} UTC for {work_order} ({case_id})")
    return booked


S2_WINDOW_PREFIX = f"{SEEDED_SLOT_PREFIX}TEL-"
S2_WINDOW_HOURS = (9, 11, 14, 16)  # the 4 UTC windows, each 2 h
# TEL-801 (the technician whose phone gets the live job) has all four windows every day, the others fewer.
S2_TECH_HOURS: dict[str, tuple[int, ...]] = {
    "TEL-801": (9, 11, 14, 16), "TEL-802": (9, 11), "TEL-803": (14, 16), "TEL-804": (11, 16),
}


def _seed_s2_windows(log: _Log) -> int:
    """S2's technicians never log in, so Reset creates their AVAILABLE windows (today's remaining ones and the next
    2 days): first deletes the ``seed-TEL-*`` rows it created before, then puts the new ones."""
    from datetime import timedelta
    table = _ddb().Table(AVAILABILITY_TABLE)
    for row in _scan_all(table, ProjectionExpression="pk, sk, slotId"):
        if str(row.get("slotId", "")).startswith(S2_WINDOW_PREFIX):
            table.delete_item(Key={"pk": row["pk"], "sk": row["sk"]})
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=2)
    created = 0
    for day in range(3):
        date = (now + timedelta(days=day)).replace(minute=0, second=0, microsecond=0)
        for tech, hours in S2_TECH_HOURS.items():
            for hour in hours:
                start = date.replace(hour=hour)
                if start <= now or start > horizon:
                    continue
                d, slot = start.strftime("%Y-%m-%d"), f"seed-{tech}-{start.strftime('%Y%m%d')}-{hour:02d}"
                table.put_item(Item={
                    "pk": f"TECHNICIAN#{tech}", "sk": f"SLOT#{d}#{slot}", "slotId": slot, "technicianId": tech,
                    "date": d, "start": f"{hour:02d}:00", "end": f"{hour + 2:02d}:00", "status": "AVAILABLE",
                    "appointmentId": None, "createdAt": _now(), "note": "Seeded S2 window", "tenantId": "meravix-broadband"})
                created += 1
    log.add(f"Created {created} S2 technician windows in {AVAILABILITY_TABLE}")
    return created


def _free_booked_windows(log: _Log, owners: dict[str, str], tenant: str) -> int:
    """C4: the tenant's BOOKED technician windows back to AVAILABLE (a window names its case; one without a case
    is meravix-foods'). Status update only, never a delete, except the windows Reset itself created."""
    from boto3.dynamodb.conditions import Attr
    table = _ddb().Table(AVAILABILITY_TABLE)
    booked = _scan_all(table, FilterExpression=Attr("status").eq("BOOKED"),
                       ProjectionExpression="pk, sk, slotId, caseId")
    freed = 0
    for it in booked:
        if not _is_mine(owners.get(it.get("caseId")), tenant):
            continue
        if str(it.get("slotId", "")).startswith(SEEDED_SLOT_PREFIX):
            # A window this Reset created for a seeded job: the next baseline puts the job on another date,
            # so delete it instead of leaving a stale window behind. The condition keeps it to those rows.
            try:
                table.delete_item(Key={"pk": it["pk"], "sk": it["sk"]},
                                  ConditionExpression="begins_with(slotId, :p)",
                                  ExpressionAttributeValues={":p": SEEDED_SLOT_PREFIX})
                freed += 1
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                    raise
            continue
        try:
            table.update_item(
                Key={"pk": it["pk"], "sk": it["sk"]},
                UpdateExpression="SET #s = :avail, appointmentId = :none REMOVE caseId, bookedAt",
                ConditionExpression="#s = :booked",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":avail": "AVAILABLE", ":booked": "BOOKED", ":none": None})
            freed += 1
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise  # changed since the scan -> leave it
    log.add(f"Freed {freed} booked availability windows in {AVAILABILITY_TABLE}")
    return freed


def _reset_inventory(log: _Log, tenant: str) -> int:
    """C7: SET onHand to the tenant's seed baseline on the existing inventory rows; nothing else changes."""
    table = _ddb().Table(c.settings.mock_inventory_table)
    done = 0
    for pk, on_hand in INVENTORY_BY_TENANT.get(tenant, {}).items():
        try:
            table.update_item(Key={"pk": pk, "sk": "META"}, UpdateExpression="SET onHand = :n",
                              ConditionExpression="attribute_exists(pk)",
                              ExpressionAttributeValues={":n": on_hand})
            done += 1
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
            log.add(f"Inventory row {pk} not found; not created", "WARN")
    log.add(f"Reset onHand on {done} inventory rows in {c.settings.mock_inventory_table}")
    return done


def _is_cached_tool_result(item: dict) -> bool:
    """C9: event/field dedup records (IDEMP#...) and Gateway tool digests (sk = tool name)."""
    return str(item.get("pk", "")).startswith("IDEMP#") or item.get("sk") in GATEWAY_TOOL_NAMES


def _clear_table(name: str, keys: tuple[str, ...], log: _Log, keep=None, full: bool = False) -> int:
    """C8/C9: delete every row of ``name`` (by key) except those ``keep(item)`` says to keep. ``full`` scans whole
    rows (``keep`` needs more than the key)."""
    table = _ddb().Table(name)
    if full:
        rows = _scan_all(table)
    else:
        rows = _scan_all(table, ProjectionExpression=", ".join(f"#k{i}" for i in range(len(keys))),
                         ExpressionAttributeNames={f"#k{i}": k for i, k in enumerate(keys)})
    doomed = [r for r in rows if keep is None or not keep(r)]
    with table.batch_writer() as bw:
        for r in doomed:
            bw.delete_item(Key={k: r[k] for k in keys})
    log.add(f"Cleared {len(doomed)} items from {name}" + (f" (kept {len(rows) - len(doomed)})" if keep else ""))
    return len(doomed)


# --- telemetry ---------------------------------------------------------------------------------

def _tenant_meta() -> dict:
    try:
        return tenants.tenant(_TENANT)
    except LookupError:
        raise _Fail(404, "NOT_FOUND", f"Unknown tenant {_TENANT}") from None


def _tenant_profile_ids() -> list[str]:
    return [p["id"] for p in _tenant_meta().get("telemetryProfiles") or [] if p.get("id") in TELEMETRY_PROFILES]


def _telemetry(body: dict, log: _Log) -> dict:
    profile = _PROFILE_ALIASES.get(str(body.get("profile", "")), str(body.get("profile", "")))
    if not _tenant_profile_ids():
        raise _Fail(400, "BAD_REQUEST", "This tenant has no telemetry profiles")
    if profile not in TELEMETRY_PROFILES or profile not in _tenant_profile_ids():
        raise _Fail(400, "BAD_REQUEST",
                    f"profile must be one of {sorted(_tenant_profile_ids())}")
    asset_id = body.get("assetId") or _tenant_meta().get("demoAssetId") or DEFAULT_ASSET_ID
    if not isinstance(asset_id, str) or not asset_id.replace("-", "").isalnum():
        raise _Fail(400, "BAD_REQUEST", "assetId must be an alphanumeric asset id, e.g. CHILLER-8821")
    topic = f"opsresolve/telemetry/{asset_id}"
    samples = TELEMETRY_PROFILES[profile]
    if profile in STREAMED_PROFILES:
        return _start_stream(profile, asset_id, samples, log)
    _publish_samples(topic, asset_id, samples, log)
    log.add(f"Telemetry profile '{profile}' sent ({len(samples)} samples)")
    return {"profile": profile, "assetId": asset_id, "published": len(samples)}


def _publish_samples(topic: str, asset_id: str, samples: list[dict[str, Any]], log: _Log,
                     pause_sec: float = 0) -> None:
    for i, s in enumerate(samples):
        if i and pause_sec:
            _sleep(pause_sec)
        payload = {"ts": _now(), "assetId": asset_id, **s}
        _iot().publish(topic=topic, qos=1, payload=json.dumps(payload))
        log.add(f"Published {topic}: supplyTempC={s['supplyTempC']} "
                f"compressorPressureVar={s['compressorPressureVar']} motorCurrentA={s['motorCurrentA']} "
                f"alarm={s['alarm']}")


def _execute_inference(payload: str) -> None:
    _sitewise().execute_action(
        targetResource={"computationModelId": SITEWISE_MODEL_ID}, resolveTo={"assetId": SITEWISE_ASSET_ID},
        actionDefinitionId=SITEWISE_INFERENCE_ACTION_ID, actionPayload={"stringValue": payload})


def _stop_inference(log: _Log) -> None:
    """Best effort and never raises: it runs in the worker's ``finally``."""
    try:
        _execute_inference(INFERENCE_STOP_PAYLOAD)
        log.add("SiteWise anomaly detection inference stopped")
    except (ClientError, BotoCoreError, TypeError, ValueError) as exc:
        code = exc.response.get("Error", {}).get("Code", "Error") if isinstance(exc, ClientError) \
            else type(exc).__name__
        log.add(f"Could not stop SiteWise inference ({code}); stop it by hand with the STOP payload", "ERROR")


def _stream_seconds(samples: list[dict[str, Any]]) -> int:
    return (len(samples) - 1) * STREAM_INTERVAL_SEC


def _start_stream(profile: str, asset_id: str, samples: list[dict[str, Any]], log: _Log) -> dict:
    """Start SiteWise inference, then hand the timed publishing to an asynchronous run of this Lambda."""
    if asset_id != DEFAULT_ASSET_ID:
        # The IoT -> SiteWise rule writes to fixed CHILLER-8821 aliases whatever the topic's asset is.
        raise _Fail(400, "BAD_REQUEST", f"profile '{profile}' only supports assetId {DEFAULT_ASSET_ID}")
    try:
        _execute_inference(INFERENCE_START_PAYLOAD)
    except (ClientError, BotoCoreError, TypeError, ValueError) as exc:
        code = exc.response.get("Error", {}).get("Code", "Error") if isinstance(exc, ClientError) \
            else type(exc).__name__
        logger.error("SiteWise inference start failed: %s", exc)
        raise _Fail(502, "INFERENCE_START_FAILED", f"Could not start SiteWise anomaly detection ({code})") from exc
    log.add("SiteWise anomaly detection inference started (PT5M)")
    event = {STREAM_WORKER_KEY: "telemetryStream", "profile": profile, "assetId": asset_id}
    try:
        _lambda().invoke(FunctionName=SCENARIO_FUNCTION_NAME, InvocationType="Event",
                         Payload=json.dumps(event).encode("utf-8"))
    except (ClientError, BotoCoreError) as exc:
        logger.error("Stream worker invoke failed: %s", exc)
        _stop_inference(log)
        raise _Fail(502, "STREAM_START_FAILED", "Could not start the telemetry stream") from exc
    duration = _stream_seconds(samples)
    log.add(f"Telemetry profile '{profile}' streaming: {len(samples)} readings, one every "
            f"{STREAM_INTERVAL_SEC}s ({duration}s); a case should open about 5 to 10 minutes from now")
    return {"profile": profile, "assetId": asset_id, "published": 0, "streaming": True,
            "samples": len(samples), "durationSec": duration}


def _remaining_sec(context: Any) -> float:
    getter = getattr(context, "get_remaining_time_in_millis", None)
    return getter() / 1000 if getter else float("inf")


def _stream_worker(event: dict, context: Any) -> dict:
    """Async run started by ``_start_stream``: publish the readings, wait for the last inference cycle, STOP.

    Never raises (an async invoke that raises would be retried and the stream would start again).
    """
    log = _Log()
    outcome = "ok"
    try:
        profile, asset_id = event["profile"], event["assetId"]
        samples = TELEMETRY_PROFILES[profile]
        needed = _stream_seconds(samples) + INFERENCE_GRACE_SEC + WORKER_SAFETY_SEC
        if _remaining_sec(context) < needed:
            outcome = "timeout_too_short"
            log.add(f"Stream needs {needed}s but only {int(_remaining_sec(context))}s remain; "
                    "raise the Lambda timeout. Nothing was published", "ERROR")
        else:
            _publish_samples(f"opsresolve/telemetry/{asset_id}", asset_id, samples, log, STREAM_INTERVAL_SEC)
            grace = max(0.0, min(INFERENCE_GRACE_SEC, _remaining_sec(context) - WORKER_SAFETY_SEC))
            log.add(f"Stream finished; waiting {int(grace)}s for the last inference cycle")
            _sleep(grace)
    except Exception:  # noqa: BLE001 - see docstring
        logger.exception("telemetry stream failed")
        outcome = "error"
    finally:
        _stop_inference(log)
    return {"worker": "telemetryStream", "outcome": outcome}


def _profiles() -> dict:
    """The tenant's telemetry profiles POST /scenario/telemetry accepts; ``id`` is the exact ``profile`` value."""
    assert set(TELEMETRY_PROFILE_INFO) == set(TELEMETRY_PROFILES), "profile metadata out of sync"
    allowed = set(_tenant_profile_ids())
    profiles = [
        {"id": pid, "label": info["label"], "description": info["description"],
         "assetId": DEFAULT_ASSET_ID, "assetModel": DEFAULT_ASSET_MODEL,
         "samples": len(TELEMETRY_PROFILES[pid]), "opensCase": info["opensCase"],
         "streaming": pid in STREAMED_PROFILES,
         "durationSec": _stream_seconds(TELEMETRY_PROFILES[pid]) if pid in STREAMED_PROFILES else 0,
         "aliases": sorted(a for a, target in _PROFILE_ALIASES.items() if target == pid)}
        for pid, info in TELEMETRY_PROFILE_INFO.items() if pid in allowed
    ]
    return {"profiles": profiles, "defaultProfileId": DEFAULT_PROFILE_ID if profiles else None,
            "count": len(profiles)}


# --- bus events --------------------------------------------------------------------------------

def _load_cases() -> list[dict]:
    from boto3.dynamodb.conditions import Attr
    items = [m for m in _scan_all(_ddb().Table(c.CASES_TABLE), FilterExpression=Attr("sk").eq("META"))
             if (m.get("tenantId") or DEFAULT_TENANT) == _TENANT]
    return sorted(items, key=lambda i: i.get("createdAt", ""), reverse=True)


def _pick_case(body: dict, states: tuple[str, ...], what: str, log: _Log) -> dict:
    cases = _load_cases()
    wanted = body.get("caseId")
    if wanted is not None:
        if not isinstance(wanted, str):
            raise _Fail(400, "BAD_REQUEST", "caseId must be a string")
        meta = next((m for m in cases if m["caseId"] == wanted), None)
        if meta is None:
            raise _Fail(404, "NOT_FOUND", f"Unknown caseId {wanted}")
        if meta["state"] not in states:
            raise _Fail(409, "CASE_NOT_APPLICABLE",
                        f"{what} needs a case in {', '.join(states)}; {wanted} is {meta['state']}")
        return meta
    for state in states:  # preference order; newest case first within a state
        for m in cases:
            if m["state"] == state:
                return m
    raise _Fail(409, "NO_APPLICABLE_CASE", f"No case in {', '.join(states)} for {what}. Run reset first.")


def _appointment(case_id: str) -> dict | None:
    """The case's newest appointment that is not cancelled (a cancelled visit has no technician to act)."""
    from boto3.dynamodb.conditions import Key
    resp = _ddb().Table(c.CASES_TABLE).query(
        KeyConditionExpression=Key("pk").eq(f"CASE#{case_id}") & Key("sk").begins_with("APPT#"))
    items = [i for i in resp.get("Items", []) if i.get("status") != "CANCELLED"]
    return items[-1] if items else None


def _put_event(source: str, detail_type: str, meta: dict, actor_id: str, payload: dict, log: _Log) -> dict:
    now = _now()
    detail = {
        "eventId": f"evt-{uuid.uuid4().hex[:12]}", "eventVersion": "1.0", "occurredAt": now,
        "caseId": meta["caseId"], "correlationId": meta.get("correlationId") or f"corr-{uuid.uuid4().hex[:8]}",
        "actor": {"type": "SYSTEM", "id": actor_id}, "payload": payload,
    }
    raw = {"source": source, "detail-type": detail_type, "detail": detail}
    normalize_event(raw)  # same validation the Agent Invoker applies; raises if the shape is unroutable
    resp = _events().put_events(Entries=[{
        "Source": source, "DetailType": detail_type, "Detail": json.dumps(detail), "EventBusName": c.BUS_NAME}])
    if resp.get("FailedEntryCount"):
        entry = (resp.get("Entries") or [{}])[0]
        raise _Fail(502, "EVENT_PUBLISH_FAILED", entry.get("ErrorMessage", "PutEvents failed"))
    log.add(f"Published '{detail_type}' for {meta['caseId']} to {c.BUS_NAME} (eventId {detail['eventId']})")
    return {"eventId": detail["eventId"], "caseId": meta["caseId"], "detailType": detail_type}


def _technician_delay(body: dict, log: _Log) -> dict:
    meta = _pick_case(body, ("SCHEDULED", "IN_SERVICE"), "Technician delay", log)
    appt = _appointment(meta["caseId"])
    tech = (appt or {}).get("technicianId", "TECH-204")
    return _put_event("tcs.opsresolve.workforce", "Technician ETA Updated", meta, "mock-wfm",
                      {"technicianId": tech, "currentJobRemainingMin": 100, "travelMin": 60}, log)


def _held_reservation(case_id: str) -> dict | None:
    """The case's newest open (HELD) part reservation in the Gateway reservations table, if any."""
    from boto3.dynamodb.conditions import Attr
    rows = _scan_all(_ddb().Table(f"{c.settings.prefix}-reservations"),
                     FilterExpression=Attr("caseId").eq(case_id) & Attr("status").eq("HELD"))
    return max(rows, key=lambda r: str(r.get("createdAt", "")), default=None)


def _part_shortage(body: dict, log: _Log) -> dict:
    meta = _pick_case(body, ("PLANNING_FIELD", "AWAITING_APPROVAL", "SCHEDULED", "DIAGNOSING"),
                      "Part shortage", log)
    held = _held_reservation(meta["caseId"]) or {}
    part_id = held.get("partId") or (meta.get("requiredPart") or {}).get("partId")
    depot_id = held.get("depotId")
    if not part_id and _TENANT == DEFAULT_TENANT:  # S1 demo values when the case holds nothing yet
        part_id, depot_id = "CTRL-BRD-500", depot_id or "DEP-CHN-02"
    if not part_id:
        raise _Fail(409, "CASE_NOT_APPLICABLE", f"{meta['caseId']} has no part reserved or required")
    return _put_event("tcs.opsresolve.inventory", "Part Pick Failed", meta, "mock-erp",
                      {"reservationId": held.get("reservationId") or f"RSV-{uuid.uuid4().int % 9000 + 1000}",
                       "depotId": depot_id or "DEP-CHN-02", "partId": part_id, "reason": "STOCK_DISCREPANCY"}, log)


def _mark_appointment_completed(case_id: str, appt: dict, log: _Log) -> None:
    """C5: the APPT# goes to COMPLETED (+ completedAt), as the mobile app's visit completion does."""
    if not appt.get("sk"):
        log.add(f"No appointment on {case_id}; nothing to mark COMPLETED")
        return
    try:
        _ddb().Table(c.CASES_TABLE).update_item(
            Key={"pk": f"CASE#{case_id}", "sk": appt["sk"]},
            UpdateExpression="SET #s = :done, completedAt = :t",
            ConditionExpression="attribute_exists(pk) AND #s <> :done",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":done": "COMPLETED", ":t": _now()})
        log.add(f"Marked {appt['sk']} on {case_id} COMPLETED")
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code == "ConditionalCheckFailedException":
            log.add(f"{appt['sk']} on {case_id} already COMPLETED")
        else:  # e.g. AccessDenied before the IAM change: still publish the event
            log.add(f"Could not mark {appt['sk']} on {case_id} COMPLETED: {code}", "WARN")


def _complete_work_order(body: dict, log: _Log) -> dict:
    meta = _pick_case(body, ("IN_SERVICE", "SCHEDULED"), "Work order completion", log)
    appt = _appointment(meta["caseId"])
    if appt is None and _TENANT == DEFAULT_TENANT:  # S1 demo values for a case with no appointment row
        appt = {"sk": "APPT#WO-77120", "technicianId": "TECH-311"}
    if appt is None:
        raise _Fail(409, "CASE_NOT_APPLICABLE",
                    f"{meta['caseId']} has no technician visit (a courier self-install ends when the customer taps Installed)")
    part = meta.get("requiredPart") or {}
    part_id = part.get("partId") or ("CTRL-BRD-500" if _TENANT == DEFAULT_TENANT else None)
    wo = appt["sk"].split("#", 1)[1]
    _mark_appointment_completed(meta["caseId"], appt, log)
    debrief = ("Replaced control board; variance 0.05 after restart; tested 20 min." if part_id == "CTRL-BRD-500"
               else f"Replaced {part.get('name') or part_id or 'the faulty unit'}; line verified after the swap.")
    return _put_event("tcs.opsresolve.fsm", "Work Order Completed", meta, "mock-fsm",
                      {"workOrderId": wo, "technicianId": appt.get("technicianId"),
                       "partsUsed": [part_id] if part_id else [], "debrief": debrief}, log)


def _guard(text: str) -> tuple[str, dict]:
    """Bedrock Guardrails on the typed text, as the inbound normalizer does (errors propagate: fail closed)."""
    resp = _bedrock().apply_guardrail(guardrailIdentifier=GUARDRAIL_ID, guardrailVersion=GUARDRAIL_VERSION,
                                      source="INPUT", content=[{"text": {"text": text}}])
    if resp.get("action") != "GUARDRAIL_INTERVENED":
        return text, {"action": "NONE"}
    outputs = resp.get("outputs") or []
    masked = outputs[0].get("text") if outputs else None
    blocked = any(a.get("action") == "BLOCKED" for a in _flatten(resp.get("assessments", [])))
    if blocked:
        return REDACTED_TEXT, {"action": "GUARDRAIL_INTERVENED", "filter": "BLOCKED"}
    return (masked or REDACTED_TEXT), {"action": "GUARDRAIL_INTERVENED", "filter": "SENSITIVE_INFORMATION"}


def _flatten(node: Any) -> list[dict]:
    """Every dict nested in ``node`` (guardrail assessments are arbitrarily nested lists/dicts)."""
    out: list[dict] = []
    if isinstance(node, dict):
        out.append(node)
        for v in node.values():
            out += _flatten(v)
    elif isinstance(node, list):
        for v in node:
            out += _flatten(v)
    return out


def _customer_message(body: dict, log: _Log) -> dict:
    """Publish ``Customer Message Received`` as if the tenant's demo contact had typed ``text`` on WhatsApp."""
    text = body.get("text", DEFAULT_CUSTOMER_TEXT)
    if not isinstance(text, str) or not text.strip() or len(text) > 1000:
        raise _Fail(400, "BAD_REQUEST", "text must be a non-empty string up to 1000 characters")
    contact_id = _tenant_meta().get("demoContactId")
    contact = _ddb().Table(c.settings.mock_contacts_table).get_item(
        Key={"pk": f"CONTACTS#{contact_id}", "sk": "META"}).get("Item") if contact_id else None
    if not contact:
        raise _Fail(409, "NOT_AVAILABLE", f"Demo contact {contact_id} is not set up for {_TENANT}")
    clean, guardrail = _guard(text.strip())
    now = _now()
    event_id = f"evt-scn-{uuid.uuid4().hex[:12]}"
    detail = {
        "eventId": event_id, "eventVersion": "1.0", "occurredAt": now, "correlationId": event_id,
        "actor": {"type": "CUSTOMER", "id": contact_id},
        "payload": {"channel": "WHATSAPP", "contactId": contact_id, "customerId": contact.get("customerId"),
                    "caseId": None, "kind": "TEXT", "intent": None, "optionId": None, "text": clean,
                    "receivedAt": now, "providerMessageId": event_id, "verified": True, "guardrail": guardrail},
    }
    normalize_event({"source": _CHANNEL_SOURCE, "detail-type": "Customer Message Received", "detail": detail})
    resp = _events().put_events(Entries=[{
        "Source": _CHANNEL_SOURCE, "DetailType": "Customer Message Received", "Detail": json.dumps(detail),
        "EventBusName": c.BUS_NAME}])
    if resp.get("FailedEntryCount"):
        raise _Fail(502, "EVENT_PUBLISH_FAILED", (resp.get("Entries") or [{}])[0].get("ErrorMessage", "PutEvents failed"))
    log.add(f"Published 'Customer Message Received' from {contact_id} to {c.BUS_NAME} (eventId {event_id}"
            + (", blocked by the guardrail" if guardrail["action"] != "NONE" and clean == REDACTED_TEXT else "") + ")")
    return {"eventId": event_id, "contactId": contact_id, "detailType": "Customer Message Received",
            "guardrail": guardrail["action"]}


_ROUTES = {
    "reset": lambda body, log: _reset(log),
    "telemetry": _telemetry,
    "technician-delay": _technician_delay,
    "part-shortage": _part_shortage,
    "complete-work-order": _complete_work_order,
    "customer-message": _customer_message,
}


def handler(event: dict, context: Any = None) -> dict:
    if event.get(STREAM_WORKER_KEY) == "telemetryStream":  # async self-invoke, not an API Gateway event
        return _stream_worker(event, context)
    global _TENANT
    log = _Log()
    _TENANT = c.tenant_id(event) or ""
    tenants.reset_cache()
    path = (event.get("rawPath") or event.get("routeKey", "").split(" ")[-1] or "").rstrip("/")
    action = path.rsplit("/", 1)[-1]
    method = (event.get("requestContext", {}).get("http", {}).get("method") or "POST").upper()
    is_profiles = (method == "GET" and path.endswith("/scenario/telemetry/profiles")) \
        or event.get("routeKey") == "GET /scenario/telemetry/profiles"
    if not is_profiles and (method != "POST" or action not in _ROUTES):
        return c.ok({"ok": False, "logs": [], "error": {"code": "NOT_FOUND", "message": f"No route {path}"}}, 404)
    if not _TENANT:
        return c.ok({"ok": False, "logs": [], "error": {"code": "FORBIDDEN", "message": c.NO_TENANT_MESSAGE}}, 403)
    if is_profiles:
        try:
            return c.ok(_profiles())
        except _Fail as f:
            return c.ok({"ok": False, "logs": [], "error": {"code": f.code, "message": f.message}}, f.status)
    body = c.parse_json_body(event) if event.get("body") else {}
    if body is None:
        return c.ok({"ok": False, "logs": [], "error": {"code": "BAD_REQUEST",
                                                          "message": "Body must be a JSON object"}}, 400)
    try:
        log.add(f"Scenario '{action}' requested" + (f" by {c.jwt_username(event)}" if c.jwt_username(event) else ""))
        if action in BUTTON_ACTIONS and action not in (_tenant_meta().get("scenarioButtons") or []):
            raise _Fail(400, "NOT_AVAILABLE", f"'{action}' is not available for {_TENANT}")
        result = _ROUTES[action](body, log)
        return c.ok({"ok": True, "logs": log.lines, "result": result})
    except _Fail as f:
        log.add(f"{f.code}: {f.message}", "ERROR")
        return c.ok({"ok": False, "logs": log.lines, "error": {"code": f.code, "message": f.message}}, f.status)
    except Exception:  # noqa: BLE001 - never leak internals to the client
        logger.exception("scenario %s failed", action)
        log.add("Internal error", "ERROR")
        return c.ok({"ok": False, "logs": log.lines,
                     "error": {"code": "INTERNAL_ERROR", "message": "Internal error"}}, 500)
