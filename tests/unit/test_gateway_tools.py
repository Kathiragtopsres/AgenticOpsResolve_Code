"""Gateway tools Lambda (``gateway tools/lambda_handler.py``): technician availability booking (NEXT SESSION B),
findAndReservePart with approvals/inventory, and UTC message times. moto DynamoDB; technician WhatsApp stubbed.

The Lambda is a standalone file (not part of the opsresolve package), loaded by path.
"""
from __future__ import annotations

import importlib.util
import pathlib
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from moto import mock_aws

from opsresolve.engines.slot_engine import propose_slots

HANDLER_PATH = pathlib.Path(__file__).resolve().parents[2] / "gateway tools" / "lambda_handler.py"

PK_SK_TABLES = ["opsresolve-dev-cases", "opsresolve-dev-field-availability", "opsresolve-dev-idempotency",
                "opsresolve-dev-mock-inventory", "opsresolve-dev-mock-technicians", "opsresolve-dev-conversations",
                "opsresolve-dev-mock-parts", "opsresolve-dev-mock-work-order-history"]
HASH_TABLES = {"opsresolve-dev-appointments": "workOrderId", "opsresolve-dev-approvals": "approvalId",
               "opsresolve-dev-reservations": "reservationId", "opsresolve-dev-transfers": "transferId"}

DAY1 = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
DAY2 = (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat()
YESTERDAY = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()


def _at(day: str, hhmm: str) -> str:
    return f"{day}T{hhmm}:00Z"


@pytest.fixture
def gw(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        for name in PK_SK_TABLES:
            ddb.create_table(
                TableName=name, BillingMode="PAY_PER_REQUEST",
                KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
                AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                      {"AttributeName": "sk", "AttributeType": "S"}],
            )
        for name, key in HASH_TABLES.items():
            ddb.create_table(
                TableName=name, BillingMode="PAY_PER_REQUEST",
                KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"}],
            )
        spec = importlib.util.spec_from_file_location("gateway_lambda_handler_under_test", HANDLER_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod._dynamodb = None
        notes = []
        monkeypatch.setattr(mod, "notify_technician",
                            lambda kind, tech, case, wo, *a, **k: notes.append((kind, tech, case, wo, k)) or
                            {"technicianId": tech, "status": "SENT", "deliveryMode": "SIMULATED"})
        mod.notes = notes
        mod.ddb = ddb
        yield mod


def _window(gw, tech: str, day: str, start: str, end: str, status: str = "AVAILABLE", slot_id: str | None = None):
    slot_id = slot_id or f"{tech}-{day}-{start}"
    gw.ddb.Table("opsresolve-dev-field-availability").put_item(Item={
        "pk": f"TECHNICIAN#{tech}", "sk": f"SLOT#{day}#{slot_id}", "slotId": slot_id, "technicianId": tech,
        "date": day, "start": start, "end": end, "status": status, "appointmentId": None, "note": None,
    })


def _windows(gw, status_filter=None):
    items = gw.ddb.Table("opsresolve-dev-field-availability").scan()["Items"]
    return [i for i in items if status_filter is None or i["status"] == status_filter]


def _case(gw, case_id: str, restore_by: str | None = None):
    item = {"pk": f"CASE#{case_id}", "sk": "META", "caseId": case_id, "state": "PLANNING_FIELD"}
    if restore_by:
        item["restoreBy"] = restore_by
    gw.ddb.Table("opsresolve-dev-cases").put_item(Item=item)


def _propose(gw, techs, duration=120, case_id="SR-1"):
    now = datetime.now(timezone.utc)
    return gw.handle_propose_appointment_slots({
        "caseId": case_id, "technicianIds": techs, "durationMin": duration,
        "windowStart": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "windowEnd": (now + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })


def _book(gw, tech, start, end, key="k1", case_id="SR-1"):
    return gw.handle_create_service_appointment({
        "caseId": case_id, "technicianId": tech, "slotStart": start, "slotEnd": end,
        "consentRef": "evt-tap-1", "idempotencyKey": key,
    })


# --- proposeAppointmentSlots ------------------------------------------------------------------------------------


def test_propose_returns_real_2h_windows_best_technician_first(gw):
    _case(gw, "SR-1")
    for start, end in (("09:00", "11:00"), ("11:00", "13:00"), ("14:00", "16:00")):
        _window(gw, "TECH-1", DAY1, start, end)
    _window(gw, "TECH-2", DAY1, "09:00", "11:00")

    out = _propose(gw, ["TECH-1", "TECH-2"])

    slots = out["slots"]
    assert len(slots) == 3  # max 3
    assert all(s["technicianId"] == "TECH-1" for s in slots)  # higher-ranked technician first
    assert [s["slotStart"] for s in slots] == [_at(DAY1, "09:00"), _at(DAY1, "11:00"), _at(DAY1, "14:00")]
    assert slots[0]["slotEnd"] == _at(DAY1, "11:00")
    assert slots[0]["availabilitySlotIds"] == [f"TECH-1-{DAY1}-09:00"]
    assert all(s["meetsSla"] is True for s in slots)  # no restoreBy on the case


def test_propose_long_job_takes_two_back_to_back_windows_never_across_lunch(gw):
    _case(gw, "SR-1")
    for start, end in (("09:00", "11:00"), ("11:00", "13:00"), ("14:00", "16:00")):
        _window(gw, "TECH-1", DAY1, start, end)

    slots = _propose(gw, ["TECH-1"], duration=180)["slots"]

    assert [(s["slotStart"], s["slotEnd"]) for s in slots] == [(_at(DAY1, "09:00"), _at(DAY1, "13:00"))]
    assert len(slots[0]["availabilitySlotIds"]) == 2


def test_propose_skips_past_and_booked_windows(gw):
    _case(gw, "SR-1")
    _window(gw, "TECH-1", YESTERDAY, "09:00", "11:00")
    _window(gw, "TECH-1", DAY1, "09:00", "11:00", status="BOOKED")
    _window(gw, "TECH-1", DAY1, "14:00", "16:00")

    slots = _propose(gw, ["TECH-1"])["slots"]

    assert [s["slotStart"] for s in slots] == [_at(DAY1, "14:00")]


def test_propose_ranks_sla_meeting_slots_first(gw):
    _case(gw, "SR-1", restore_by=_at(DAY1, "13:00"))
    _window(gw, "TECH-1", DAY2, "09:00", "11:00")   # best technician, but after restoreBy
    _window(gw, "TECH-2", DAY1, "09:00", "11:00")   # meets the SLA

    slots = _propose(gw, ["TECH-1", "TECH-2"])["slots"]

    assert slots[0]["technicianId"] == "TECH-2" and slots[0]["meetsSla"] is True
    assert slots[1]["technicianId"] == "TECH-1" and slots[1]["meetsSla"] is False


def test_propose_rejects_jobs_longer_than_two_windows(gw):
    out = _propose(gw, ["TECH-1"], duration=241)
    assert out["error"]["code"] == "VALIDATION_ERROR"


def test_propose_with_no_free_windows_is_no_technician(gw):
    _case(gw, "SR-1")
    out = _propose(gw, ["TECH-9"])
    assert out["error"]["code"] == "NO_TECHNICIAN"


def test_rank_slots_matches_slot_engine():
    spec = importlib.util.spec_from_file_location("gw_rank_parity", HANDLER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    t = datetime(2026, 10, 14, 9, tzinfo=timezone.utc)
    cands = [
        {"slotId": "a", "technicianId": "T1", "start": t + timedelta(days=1), "end": t + timedelta(days=1, hours=2), "score": 3},
        {"slotId": "b", "technicianId": "T2", "start": t, "end": t + timedelta(hours=2), "score": 1},
        {"slotId": "c", "technicianId": "T3", "start": t + timedelta(hours=2), "end": t + timedelta(hours=4), "score": 2},
        {"slotId": "d", "technicianId": "T1", "start": t + timedelta(hours=5), "end": t + timedelta(hours=7), "score": 3},
    ]
    restore_by = t + timedelta(hours=4)
    ours = [c["slotId"] for c in mod._rank_slots(cands, restore_by)]
    engine = [c["slotId"] for c in propose_slots(cands, restore_by)]
    assert ours == engine == ["c", "b", "d"]  # SLA-meeting first (by score), then by score, then earliest


# --- createServiceAppointment / reassignTechnician / updateWorkOrder CANCELLED -------------------------------------


def test_create_books_windows_and_writes_mobile_job(gw):
    _window(gw, "TECH-1", DAY1, "09:00", "11:00")
    _window(gw, "TECH-1", DAY1, "11:00", "13:00")

    out = _book(gw, "TECH-1", _at(DAY1, "09:00"), _at(DAY1, "13:00"))

    assert out["status"] == "SCHEDULED"
    wo = out["workOrderId"]
    booked = _windows(gw, "BOOKED")
    assert len(booked) == 2 and all(w["appointmentId"] == wo for w in booked)
    appt = gw.ddb.Table("opsresolve-dev-appointments").get_item(Key={"workOrderId": wo})["Item"]
    assert len(appt["availabilitySlots"]) == 2
    job = gw.ddb.Table("opsresolve-dev-cases").get_item(Key={"pk": "CASE#SR-1", "sk": f"APPT#{wo}"})["Item"]
    assert job["technicianId"] == "TECH-1" and job["status"] == "SCHEDULED"
    assert gw.notes[0][0] == "ASSIGNED"


def test_create_never_double_books(gw):
    _window(gw, "TECH-1", DAY1, "09:00", "11:00")

    first = _book(gw, "TECH-1", _at(DAY1, "09:00"), _at(DAY1, "11:00"), key="k1")
    second = _book(gw, "TECH-1", _at(DAY1, "09:00"), _at(DAY1, "11:00"), key="k2", case_id="SR-2")

    assert first["status"] == "SCHEDULED"
    assert second["error"]["code"] == "CONFLICT_STATE"
    assert len(_windows(gw, "BOOKED")) == 1
    assert len(gw.ddb.Table("opsresolve-dev-appointments").scan()["Items"]) == 1
    # A failed booking is not cached: the same key can book once the window is free.
    assert gw.ddb.Table("opsresolve-dev-idempotency").get_item(Key={"pk": "k2", "sk": "createServiceAppointment"}).get("Item") is None


def test_create_rejects_slot_not_matching_windows(gw):
    _window(gw, "TECH-1", DAY1, "11:00", "13:00")
    _window(gw, "TECH-1", DAY1, "14:00", "16:00")
    out = _book(gw, "TECH-1", _at(DAY1, "11:00"), _at(DAY1, "16:00"))  # spans the lunch gap
    assert out["error"]["code"] == "CONFLICT_STATE"
    assert _windows(gw, "BOOKED") == []


def test_reassign_moves_the_booking(gw):
    _window(gw, "TECH-1", DAY1, "09:00", "11:00")
    _window(gw, "TECH-2", DAY1, "09:00", "11:00")
    wo = _book(gw, "TECH-1", _at(DAY1, "09:00"), _at(DAY1, "11:00"))["workOrderId"]

    out = gw.handle_reassign_technician({"workOrderId": wo, "newTechnicianId": "TECH-2", "reason": "ETA risk",
                                         "idempotencyKey": "r1"})

    assert out["status"] == "REASSIGNED"
    by_tech = {w["technicianId"]: w for w in _windows(gw)}
    assert by_tech["TECH-1"]["status"] == "AVAILABLE" and by_tech["TECH-1"]["appointmentId"] is None
    assert by_tech["TECH-2"]["status"] == "BOOKED" and by_tech["TECH-2"]["appointmentId"] == wo
    job = gw.ddb.Table("opsresolve-dev-cases").get_item(Key={"pk": "CASE#SR-1", "sk": f"APPT#{wo}"})["Item"]
    assert job["technicianId"] == "TECH-2"


def test_reassign_to_busy_technician_changes_nothing(gw):
    _window(gw, "TECH-1", DAY1, "09:00", "11:00")
    wo = _book(gw, "TECH-1", _at(DAY1, "09:00"), _at(DAY1, "11:00"))["workOrderId"]

    out = gw.handle_reassign_technician({"workOrderId": wo, "newTechnicianId": "TECH-2", "reason": "x",
                                         "idempotencyKey": "r1"})

    assert out["error"]["code"] == "CONFLICT_STATE"
    assert _windows(gw, "BOOKED")[0]["technicianId"] == "TECH-1"


def test_cancel_frees_windows(gw):
    _window(gw, "TECH-1", DAY1, "09:00", "11:00")
    _window(gw, "TECH-1", DAY1, "11:00", "13:00")
    wo = _book(gw, "TECH-1", _at(DAY1, "09:00"), _at(DAY1, "13:00"))["workOrderId"]

    out = gw.handle_update_work_order({"workOrderId": wo, "status": "CANCELLED", "idempotencyKey": "c1"})

    assert out["availabilityFreed"] == 2
    assert _windows(gw, "BOOKED") == []
    appt = gw.ddb.Table("opsresolve-dev-appointments").get_item(Key={"workOrderId": wo})["Item"]
    assert appt["status"] == "CANCELLED"


# --- findAndReservePart ----------------------------------------------------------------------------------------


def _stock(gw, depot, part, on_hand, reserved=0):
    gw.ddb.Table("opsresolve-dev-mock-inventory").put_item(Item={
        "pk": f"INV#{depot}#{part}", "sk": "META", "depotId": depot, "partId": part, "onHand": on_hand,
        "reserved": reserved})


def _approval(gw, approval_id, status="APPROVED", action="PART_REQUEST:CTRL-BRD-500", **extra):
    gw.ddb.Table("opsresolve-dev-approvals").put_item(Item={
        "approvalId": approval_id, "caseId": "SR-1", "action": action, "status": status, **extra})


def _reserve(gw, key="p1", **extra):
    return gw.handle_find_and_reserve_part({"caseId": "SR-1", "partId": "CTRL-BRD-500", "mode": "CONFIRM",
                                            "idempotencyKey": key, **extra})


def test_part_over_threshold_without_approval_needs_approval(gw):
    gw.ddb.Table("opsresolve-dev-mock-parts").put_item(Item={"pk": "PARTS#CTRL-BRD-500", "sk": "META", "partId": "CTRL-BRD-500", "unitValueUsd": 1200})
    _stock(gw, "DEP-CHN-01", "CTRL-BRD-500", 3)
    assert _reserve(gw)["error"]["code"] == "NEEDS_APPROVAL"


def test_approved_part_request_reserves_elsewhere_transfers_and_notifies(gw):
    gw.ddb.Table("opsresolve-dev-mock-parts").put_item(Item={"pk": "PARTS#CTRL-BRD-500", "sk": "META", "partId": "CTRL-BRD-500", "unitValueUsd": 1200})
    gw.ddb.Table("opsresolve-dev-mock-technicians").put_item(Item={
        "pk": "TECHNICIANS#TECH-204", "sk": "META", "technicianId": "TECH-204", "homeDepotId": "DEP-CHN-01"})
    _stock(gw, "DEP-CHN-01", "CTRL-BRD-500", 1, reserved=1)   # home depot: nothing free
    _stock(gw, "DEP-CHN-02", "CTRL-BRD-500", 4)
    _approval(gw, "APR-FLD-1", requestedBy="TECH-204", qty=1, source="FIELD_APP")

    out = _reserve(gw, approvalId="APR-FLD-1")

    assert out["depotId"] == "DEP-CHN-02" and out["status"] == "CONFIRMED"
    assert out["transfer"]["status"] == "IN_TRANSIT"
    assert gw.ddb.Table("opsresolve-dev-transfers").scan()["Items"][0]["toDepotId"] == "DEP-CHN-01"
    assert gw.notes[0][0] == "PART_APPROVED" and gw.notes[0][1] == "TECH-204"
    # Stock is never written.
    inv = gw.ddb.Table("opsresolve-dev-mock-inventory").get_item(Key={"pk": "INV#DEP-CHN-02#CTRL-BRD-500", "sk": "META"})["Item"]
    assert inv["onHand"] == 4


def test_rejected_approval_is_policy_denied(gw):
    _stock(gw, "DEP-CHN-01", "CTRL-BRD-500", 3)
    _approval(gw, "APR-FLD-2", status="REJECTED")
    assert _reserve(gw, approvalId="APR-FLD-2")["error"]["code"] == "POLICY_DENIED"


def test_no_stock_anywhere_is_part_unavailable(gw):
    gw.ddb.Table("opsresolve-dev-mock-parts").put_item(Item={"pk": "PARTS#CTRL-BRD-500", "sk": "META", "partId": "CTRL-BRD-500", "unitValueUsd": 100})
    _stock(gw, "DEP-CHN-01", "CTRL-BRD-500", 0)
    assert _reserve(gw)["error"]["code"] == "PART_UNAVAILABLE"


def test_lambda_dispatch_passes_approval_id(gw):
    gw.ddb.Table("opsresolve-dev-mock-parts").put_item(Item={"pk": "PARTS#CTRL-BRD-500", "sk": "META", "partId": "CTRL-BRD-500", "unitValueUsd": 100})
    _stock(gw, "DEP-CHN-01", "CTRL-BRD-500", 2)
    _approval(gw, "APR-3")
    import json
    resp = gw.lambda_handler({"tool_name": "findAndReservePart", "caseId": "SR-1", "partId": "CTRL-BRD-500",
                              "mode": "HOLD", "approvalId": "APR-3", "idempotencyKey": "p9"}, None)
    body = json.loads(resp["body"])
    assert body["status"] == "HELD" and body["depotId"] == "DEP-CHN-01"
    res = gw.ddb.Table("opsresolve-dev-reservations").scan()["Items"][0]
    assert res["approvalId"] == "APR-3"


# --- UTC display (B5) ------------------------------------------------------------------------------------------


def test_message_times_are_utc():
    spec = importlib.util.spec_from_file_location("gw_tz", HANDLER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.fmt_slot("2026-10-14T09:00:00Z", "2026-10-14T11:00:00Z") == "Wed 14 Oct 09:00-11:00 UTC"
    opts = mod._options({"slots": [{"technicianId": "TECH-1", "slotStart": "2026-10-14T09:00:00Z",
                                    "slotEnd": "2026-10-14T11:00:00Z", "availabilitySlotIds": ["x"]}]})
    assert opts[0]["title"] == "Wed 14 Oct 09:00-11:00 UTC"
    assert opts[0]["id"] == "TECH-1~2026-10-14T09:00:00Z~2026-10-14T11:00:00Z"


# --- closeServiceCase (case store layout) and knowledge-base retrieval -----------------------------

def _case_meta(gw, case_id: str, state: str, version: int = 5):
    gw.ddb.Table("opsresolve-dev-cases").put_item(Item={
        "pk": f"CASE#{case_id}", "sk": "META", "caseId": case_id, "state": state, "version": version,
        "gsi1pk": f"STATE#{state}", "gsi1sk": "P1#x"})


def _close_args(case_id: str, result: str = "PASSED") -> dict:
    return {"caseId": case_id, "verificationResult": result, "idempotencyKey": f"close-{case_id}-{result}"}


def _meta_of(gw, case_id: str) -> dict:
    return gw.ddb.Table("opsresolve-dev-cases").get_item(Key={"pk": f"CASE#{case_id}", "sk": "META"}).get("Item")


def test_close_service_case_updates_the_real_case_item(gw):
    _case_meta(gw, "SR-C1", "VERIFYING", version=5)
    out = gw.handle_close_service_case(_close_args("SR-C1"))
    assert out["status"] == "CLOSED" and "error" not in out
    meta = _meta_of(gw, "SR-C1")
    assert meta["state"] == "CLOSED" and meta["gsi1pk"] == "STATE#CLOSED" and meta["version"] == 6
    assert meta["closedAt"].endswith("Z")
    assert _meta_of(gw, "SR-C1") and gw.ddb.Table("opsresolve-dev-cases").scan()["Count"] == 1  # no stray item


def test_close_service_case_rejects_wrong_state_unknown_case_and_failed_verification(gw):
    _case_meta(gw, "SR-C2", "IN_SERVICE")
    assert gw.handle_close_service_case(_close_args("SR-C2"))["error"]["code"] == "CONFLICT_STATE"
    assert _meta_of(gw, "SR-C2")["state"] == "IN_SERVICE"

    assert gw.handle_close_service_case(_close_args("SR-NOPE"))["error"]["code"] == "CONFLICT_STATE"
    assert _meta_of(gw, "SR-NOPE") is None  # nothing created

    _case_meta(gw, "SR-C3", "VERIFYING")
    assert gw.handle_close_service_case(_close_args("SR-C3", "FAILED"))["error"]["code"] == "CONFLICT_STATE"
    assert _meta_of(gw, "SR-C3")["state"] == "VERIFYING"


class _KbClient:
    def __init__(self, *failures):
        self.failures, self.calls = list(failures), []

    def retrieve(self, **kwargs):
        self.calls.append(next(iter(kwargs["retrievalConfiguration"])))
        if self.failures:
            raise self.failures.pop(0)
        return {"retrievalResults": []}


def _validation_error(message: str):
    from botocore.exceptions import ClientError
    return ClientError({"Error": {"Code": "ValidationException", "Message": message}}, "Retrieve")


def test_kb_retrieve_uses_managed_search_first(gw, monkeypatch):
    client = _KbClient()
    monkeypatch.setattr(gw, "get_bedrock_agent_runtime", lambda: client)
    gw._kb_retrieve("q", 3)
    assert client.calls == ["managedSearchConfiguration"]


def test_kb_retrieve_falls_back_to_vector_search(gw, monkeypatch):
    from botocore.exceptions import ParamValidationError
    for failure in (_validation_error("Incompatible configuration: managedSearchConfiguration is not supported"),
                    ParamValidationError(report="Unknown parameter managedSearchConfiguration")):
        client = _KbClient(failure)
        monkeypatch.setattr(gw, "get_bedrock_agent_runtime", lambda c=client: c)
        gw._kb_retrieve("q", 3)
        assert client.calls == ["managedSearchConfiguration", "vectorSearchConfiguration"]


def test_kb_retrieve_does_not_hide_other_errors(gw, monkeypatch):
    from botocore.exceptions import ClientError
    client = _KbClient(_validation_error("filter is malformed"))
    monkeypatch.setattr(gw, "get_bedrock_agent_runtime", lambda: client)
    with pytest.raises(ClientError):
        gw._kb_retrieve("q", 3)
    assert client.calls == ["managedSearchConfiguration"]
