"""Control loop end to end (agents/orchestrator.py + RuleBasedPlanner) against moto DynamoDB.

Covers NEXT SESSION A1-A4: approval decided resumes, work order completed -> VERIFYING -> CSAT -> CLOSED, slot tap
books (and a failed booking stays put), escalated cases only record events, free text is acknowledged.

The specialist LLM is replaced by a stub for AGENT goals; DIRECT goals run the real ``direct_tools`` path with the
Gateway call patched, so the exact tool arguments are asserted.
"""
from __future__ import annotations

import uuid

import boto3
import pytest
from boto3.dynamodb.conditions import Key
from moto import mock_aws

from opsresolve.agents import direct_tools, orchestrator
from opsresolve.agents.orchestrator import StrandsSpecialistInvoker, classify_trigger, handle_event
from opsresolve.common import case_store, dynamo
from opsresolve.common.config import settings


class _Invoker(StrandsSpecialistInvoker):
    """Real DIRECT path; AGENT goals return a canned summary instead of calling Bedrock."""

    def __init__(self):
        self.calls = []

    def invoke(self, spec, goal, envelope, correlation_id):
        self.calls.append((spec.name, goal))
        if goal.mode == "DIRECT":
            return super().invoke(spec, goal, envelope, correlation_id)
        return {"summary": f"{spec.name} done"}


@pytest.fixture
def store(monkeypatch):
    dynamo._resource.cache_clear()
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name=settings.region)
        for name in (settings.cases_table, settings.idempotency_table, settings.mock_contacts_table,
                     settings.mock_assets_table, settings.mock_technicians_table):
            extra = {}
            if name == settings.cases_table:
                extra = {"GlobalSecondaryIndexes": [
                    {"IndexName": n, "KeySchema": [{"AttributeName": h, "KeyType": "HASH"},
                                                   {"AttributeName": r, "KeyType": "RANGE"}],
                     "Projection": {"ProjectionType": "ALL"}}
                    for n, h, r in (("GSI1", "gsi1pk", "gsi1sk"), ("GSI2", "gsi2pk", "gsi2sk"))]}
            attrs = ["pk", "sk"] + (["gsi1pk", "gsi1sk", "gsi2pk", "gsi2sk"] if extra else [])
            ddb.create_table(
                TableName=name, BillingMode="PAY_PER_REQUEST",
                KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
                AttributeDefinitions=[{"AttributeName": a, "AttributeType": "S"} for a in attrs],
                **extra,
            )
        ddb.create_table(
            TableName=settings.approvals_table, BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "approvalId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "approvalId", "AttributeType": "S"}],
        )
        contacts = ddb.Table(settings.mock_contacts_table)
        contacts.put_item(Item={"pk": "CONTACTS#CON-78", "sk": "META", "contactId": "CON-78",
                                "customerId": "CUST-1042", "isPrimary": False})
        contacts.put_item(Item={"pk": "CONTACTS#CON-77", "sk": "META", "contactId": "CON-77",
                                "customerId": "CUST-1042", "isPrimary": True})

        ddb.Table(settings.mock_assets_table).put_item(
            Item={"pk": "ASSETS#CHILLER-8821", "sk": "META", "assetId": "CHILLER-8821", "model": "CX-500"})

        techs = ddb.Table(settings.mock_technicians_table)
        for tid, eta, util in (("TECH-204", 40, 0.2), ("TECH-311", 90, 0.5)):
            techs.put_item(Item={
                "pk": f"TECHNICIANS#{tid}", "sk": "META", "technicianId": tid, "name": tid,
                "skills": {"HVAC-CHILLER-L2": 5}, "certifications": {"CX-500": "2099-12-31"},
                "region": "SOUTH", "etaMinToPlantA": eta, "ftfRate": {"CX-500": "0.9"}, "utilisation": str(util),
            })

        gateway_calls = []
        responses = {}

        def fake_gateway(name, arguments):
            gateway_calls.append((name, dict(arguments)))
            return responses.get(name, {"status": "OK"})

        monkeypatch.setattr(direct_tools, "_gateway_call", fake_gateway)
        timers = []  # lifecycle timers the orchestrator asked EventBridge Scheduler for
        monkeypatch.setattr(orchestrator.case_tools, "schedule_timer",
                            lambda payload: timers.append(payload) or {"status": "SCHEDULED"})
        yield {"ddb": ddb, "calls": gateway_calls, "responses": responses, "timers": timers}
    dynamo._resource.cache_clear()


def _case_in(state: str, case_id: str = "SR-T1") -> str:
    case_store.open_case(case_id, customer_id="CUST-1042", asset_id="CHILLER-8821", correlation_id="corr-1",
                         trigger={"type": "TEST"})
    case_store.get_table(settings.cases_table).update_item(
        Key={"pk": f"CASE#{case_id}", "sk": "META"},
        UpdateExpression="SET #s = :s", ExpressionAttributeNames={"#s": "state"},
        ExpressionAttributeValues={":s": state},
    )
    return case_id


def _event(detail_type: str, case_id: str, payload: dict, source: str = "tcs.opsresolve.test") -> dict:
    eid = f"evt-{uuid.uuid4().hex[:8]}"
    return {"source": source, "detail-type": detail_type,
            "detail": {"eventId": eid, "caseId": case_id, "correlationId": eid, "payload": payload}}


def _timer(purpose: str, case_id: str, **extra) -> dict:
    return _event("Timer Fired", case_id, {"purpose": purpose, **extra}, "tcs.opsresolve.timer")


def _timeline(case_id: str) -> list[dict]:
    items = case_store.get_table(settings.cases_table).query(
        KeyConditionExpression=Key("pk").eq(f"CASE#{case_id}") & Key("sk").begins_with("EVT#"))["Items"]
    return sorted(items, key=lambda i: i["sk"])


def _types(case_id: str) -> list[str]:
    return [e["type"] for e in _timeline(case_id)]


def _state(case_id: str) -> str:
    return case_store.get_case_metadata(case_id).state


# --- classify_trigger ---------------------------------------------------------------------------------------------


def test_classify_trigger_carries_detail_type_and_whitelisted_payload():
    ev = _event("Approval Decided", "SR-1", {"approvalId": "A1", "decision": "APPROVED", "action": "PART_REQUEST:P",
                                             "decidedBy": "manager", "phone": "+10000000000"})
    trig = classify_trigger(ev)
    assert trig["type"] == "APPROVAL" and trig["detailType"] == "Approval Decided"
    assert trig["payload"] == {"approvalId": "A1", "decision": "APPROVED", "action": "PART_REQUEST:P"}


# --- A3: escalated cases only record --------------------------------------------------------------------------------


def test_escalated_case_records_event_without_transition(store):
    case_id = _case_in("ESCALATED_HUMAN")
    res = handle_event(_event("Customer Message Received", case_id, {"kind": "TEXT", "text": "any update?"}),
                       case_id=case_id, specialist_invoker=_Invoker())
    assert res.to_state == "ESCALATED_HUMAN" and not res.escalated
    assert "CASE_STATE_CHANGED" not in _types(case_id)
    assert "EVENT_RECORDED" in _types(case_id)


def test_escalated_case_failsafe_never_re_escalates(store):
    class Boom:
        def plan(self, envelope, trigger):
            raise RuntimeError("planner down")

    case_id = _case_in("ESCALATED_HUMAN")
    res = handle_event(_event("Approval Decided", case_id, {"decision": "APPROVED"}), case_id=case_id,
                       planner=Boom(), specialist_invoker=_Invoker())
    assert res.escalated and res.to_state == "ESCALATED_HUMAN"
    assert _types(case_id).count("CASE_STATE_CHANGED") == 0
    assert "ESCALATION_NOTED" in _types(case_id)


# --- A2: approval decided resumes ---------------------------------------------------------------------------------


def test_approved_technician_part_request_reserves_and_stays_in_service(store):
    case_id = _case_in("IN_SERVICE", "SR-FIELD-TECH204-02")
    ev = _event("Approval Decided", case_id,
                {"approvalId": "APR-FLD-1", "decision": "APPROVED", "action": "PART_REQUEST:CTRL-BRD-500"})
    res = handle_event(ev, case_id=case_id, specialist_invoker=_Invoker())

    assert not res.escalated and res.to_state == "IN_SERVICE"
    assert store["calls"][0][0] == "findAndReservePart"
    args = store["calls"][0][1]
    assert args["partId"] == "CTRL-BRD-500" and args["approvalId"] == "APR-FLD-1" and args["mode"] == "CONFIRM"
    assert args["caseId"] == case_id and args["idempotencyKey"] == f"{case_id}:part:APR-FLD-1"
    assert "PART_REQUEST_DECIDED" in _types(case_id)
    assert "CASE_STATE_CHANGED" not in _types(case_id)


def test_rejected_part_request_only_records(store):
    case_id = _case_in("IN_SERVICE")
    ev = _event("Approval Decided", case_id,
                {"approvalId": "APR-FLD-2", "decision": "REJECTED", "action": "PART_REQUEST:CTRL-BRD-500"})
    res = handle_event(ev, case_id=case_id, specialist_invoker=_Invoker())
    assert res.to_state == "IN_SERVICE" and not res.escalated
    assert store["calls"] == []
    assert "PART_REQUEST_DECIDED" in _types(case_id)


# --- A1: work order completed -> VERIFYING -> CSAT -> CLOSED --------------------------------------------------------


def test_work_order_completed_then_csat_closes(store):
    case_id = _case_in("IN_SERVICE")
    res = handle_event(_event("Work Order Completed", case_id, {"workOrderId": "WO-1"}, "tcs.opsresolve.fsm"),
                       case_id=case_id, specialist_invoker=_Invoker())
    assert res.to_state == "VERIFYING" and not res.escalated
    name, args = store["calls"][0]
    assert name == "sendCustomerMessage" and args["intent"] == "CSAT"
    assert args["contactId"] == "CON-77"  # primary contact resolved from the sentinel

    assert sorted(t["purpose"] for t in store["timers"]) == ["CSAT_WAIT_CAP", "VERIFY_WINDOW"]

    # CSAT first: stored, but the case waits for the verification window.
    res = handle_event(_event("Customer Action Selected", case_id, {"kind": "POSTBACK", "intent": "CSAT", "optionId": "5"}),
                       case_id=case_id, specialist_invoker=_Invoker())
    assert res.to_state == "VERIFYING"
    csat = [e for e in _timeline(case_id) if e["type"] == "CSAT_RECEIVED"]
    assert csat and csat[0]["detail"]["score"] == 5
    assert case_store.get_case_context(case_id, trigger={})["lifecycle"]["csatScore"] == 5

    res = handle_event(_timer("VERIFY_WINDOW", case_id), case_id=case_id, specialist_invoker=_Invoker())
    assert res.to_state == "CLOSED"


def test_work_order_completed_on_scheduled_goes_via_in_service(store):
    case_id = _case_in("SCHEDULED")
    res = handle_event(_event("Work Order Completed", case_id, {"workOrderId": "WO-1"}), case_id=case_id,
                       specialist_invoker=_Invoker())
    assert res.to_state == "VERIFYING"
    changes = [e["detail"] for e in _timeline(case_id) if e["type"] == "CASE_STATE_CHANGED"]
    # EVT# sort keys are second-resolution + a random id, so same-second events come back in any order.
    assert sorted((c["from"], c["to"]) for c in changes) == [("IN_SERVICE", "VERIFYING"), ("SCHEDULED", "IN_SERVICE")]


def test_typed_csat_digit_after_window_closes_verifying_case(store):
    case_id = _case_in("VERIFYING")
    # Window first (no score yet): the case waits for the CSAT, then the typed digit closes it.
    res = handle_event(_timer("VERIFY_WINDOW", case_id), case_id=case_id, specialist_invoker=_Invoker())
    assert res.to_state == "VERIFYING"
    res = handle_event(_event("Customer Message Received", case_id, {"kind": "TEXT", "text": "4"}), case_id=case_id,
                       specialist_invoker=_Invoker())
    assert res.to_state == "CLOSED"


# --- A4 / B4: slot tap books ---------------------------------------------------------------------------------------


def _tap(case_id, option):
    return _event("Customer Action Selected", case_id, {"kind": "POSTBACK", "intent": "BOOK_SLOT", "optionId": option})


def test_book_slot_tap_books_and_schedules(store):
    case_id = _case_in("PLANNING_FIELD")
    store["responses"]["createServiceAppointment"] = {"workOrderId": "wo-1", "status": "SCHEDULED"}
    ev = _tap(case_id, "TECH-204~2026-10-14T09:00:00Z~2026-10-14T11:00:00Z")
    res = handle_event(ev, case_id=case_id, specialist_invoker=_Invoker())

    assert res.to_state == "SCHEDULED"
    name, args = store["calls"][0]
    assert name == "createServiceAppointment"
    assert (args["technicianId"], args["slotStart"], args["slotEnd"]) == (
        "TECH-204", "2026-10-14T09:00:00Z", "2026-10-14T11:00:00Z")
    assert args["consentRef"] == ev["detail"]["eventId"]


def test_failed_booking_stays_planning_field(store):
    case_id = _case_in("PLANNING_FIELD")
    store["responses"]["createServiceAppointment"] = {"error": {"code": "CONFLICT_STATE", "message": "taken"}}
    res = handle_event(_tap(case_id, "TECH-204~2026-10-14T09:00:00Z~2026-10-14T11:00:00Z"), case_id=case_id,
                       specialist_invoker=_Invoker())
    assert res.to_state == "PLANNING_FIELD" and not res.escalated
    assert "GOAL_FAILED" in _types(case_id)


def test_unparseable_slot_tap_is_recorded_only(store):
    case_id = _case_in("PLANNING_FIELD")
    res = handle_event(_tap(case_id, "3"), case_id=case_id, specialist_invoker=_Invoker())
    assert res.to_state == "PLANNING_FIELD" and store["calls"] == []


def test_free_text_is_acknowledged_and_state_kept(store):
    case_id = _case_in("SCHEDULED")
    inv = _Invoker()
    res = handle_event(_event("Customer Message Received", case_id, {"kind": "TEXT", "text": "running late?"}),
                       case_id=case_id, specialist_invoker=inv)
    assert res.to_state == "SCHEDULED" and not res.escalated
    assert inv.calls[0][0] == "CommunicationAgent" and inv.calls[0][1].inputs["contactId"] == "CON-77"


def test_planning_field_plan_offers_availability_slots(store):
    case_id = _case_in("PLANNING_FIELD")
    inv = _Invoker()
    res = handle_event(_event("Technician ETA Updated", case_id, {}), case_id=case_id, specialist_invoker=inv)
    assert res.to_state == "PLANNING_FIELD"
    names = [c[0] for c in inv.calls]
    assert names == ["PartsAgent", "TechnicianMatchingAgent", "SchedulingDispatchAgent", "CommunicationAgent"]
    match_goal = inv.calls[1][1]  # the matching tool gets real arguments, not LLM guesses
    assert match_goal.inputs["assetModel"] == "CX-500"
    assert match_goal.inputs["requiredSkill"] == "HVAC-CHILLER-L2"
    assert match_goal.inputs["region"] == "SOUTH" and match_goal.inputs["windowStart"].endswith("Z")
    slots_goal = inv.calls[2][1]
    assert match_goal.mode == "DIRECT"
    assert slots_goal.inputs["technicianIds"] == ["TECH-204", "TECH-311"]  # ranked by the deterministic tool
    assert slots_goal.inputs["durationMin"] == 120
    assert slots_goal.inputs["windowStart"].endswith("Z") and slots_goal.inputs["windowEnd"].endswith("Z")


def test_no_qualified_technician_escalates_with_reason(store):
    store["ddb"].Table(settings.mock_assets_table).put_item(
        Item={"pk": "ASSETS#CHILLER-8821", "sk": "META", "assetId": "CHILLER-8821", "model": "CX-500"})
    for tid in ("TECH-204", "TECH-311"):  # nobody holds the skill any more
        store["ddb"].Table(settings.mock_technicians_table).update_item(
            Key={"pk": f"TECHNICIANS#{tid}", "sk": "META"}, UpdateExpression="SET skills = :s",
            ExpressionAttributeValues={":s": {"OTHER": 1}})
    case_id = _case_in("PLANNING_FIELD")
    inv = _Invoker()
    res = handle_event(_event("Technician ETA Updated", case_id, {}), case_id=case_id, specialist_invoker=inv)
    assert res.to_state == "ESCALATED_HUMAN" and res.escalated
    assert "SchedulingDispatchAgent" not in [c[0] for c in inv.calls]
    reasons = " ".join(e.get("summary", "") + str(e.get("detail", "")) for e in _timeline(case_id))
    assert "No qualified technician for CX-500/HVAC-CHILLER-L2/SOUTH" in reasons


def test_ranked_technicians_sentinel_resolves_from_prior_results():
    envelope = {"caseId": "SR-1", "priorResults": [
        {"specialist": "PartsAgent", "status": "OK", "result": {"summary": "x"}},
        {"specialist": "TechnicianMatchingAgent", "status": "OK", "result": {"results": {
            "findQualifiedTechnicians": {"candidates": [{"technicianId": "T2"}, {"technicianId": "T1"}]}}}},
    ]}
    out = direct_tools.resolve_inputs({"technicianIds": "$rankedTechnicianIds"}, envelope, "c")
    assert out["technicianIds"] == ["T2", "T1"]
    assert "technicianIds" not in direct_tools.resolve_inputs({"technicianIds": "$rankedTechnicianIds"}, {}, "c")


def test_matching_goal_is_direct_and_scheduling_gets_ranked_ids():
    from opsresolve.agents.planner import RANKED_TECHNICIANS, RuleBasedPlanner
    goals = RuleBasedPlanner()._field_planning_goals("SR-1")
    assert goals[0].specialist == "TechnicianMatchingAgent" and goals[0].mode == "DIRECT"
    assert goals[1].inputs["technicianIds"] == RANKED_TECHNICIANS


def test_direct_goal_with_local_tool_is_refused(store):
    from opsresolve.agents.planner import Goal, Plan
    from opsresolve.agents.orchestrator import InvocationResult, OrchestratorError, _act

    plan = Plan(version=1, goals=[Goal("AssetContextAgent", ["getAssetContext"], "x", mode="DIRECT")],
                target_state=None, rationale="t")
    result = InvocationResult(case_id="SR-1", correlation_id="c", from_state="OPEN", to_state="OPEN", plan=plan)
    with pytest.raises(OrchestratorError, match="Gateway"):
        _act("SR-1", {}, plan, "c", result, _Invoker())
    assert orchestrator.MAX_SPECIALIST_CALLS == 4


# --- Duplicate producers: one open case per asset -------------------------------------------------------------------


def _anomaly(payload: dict) -> dict:
    return _event("Telemetry Anomaly Detected", None, payload)


def _metas() -> list[dict]:
    items = case_store.get_table(settings.cases_table).scan()["Items"]
    return [i for i in items if i["sk"] == "META"]


def test_second_anomaly_on_same_asset_is_merged_not_duplicated(store):
    case_id = _case_in("DIAGNOSING")
    ev = _anomaly({"assetId": "CHILLER-8821", "customerId": "CUST-1042"})
    ev["detail"].pop("caseId")
    res = handle_event(ev, specialist_invoker=_Invoker())
    assert res.case_id == case_id
    assert len(_metas()) == 1
    assert "TRIGGER_MERGED" in _types(case_id)
    assert _state(case_id) == "DIAGNOSING" and not res.escalated
    assert "EVENT_RECORDED" not in _types(case_id)  # merged anomalies add no second timeline entry
    # A further anomaly inside the 15 min window adds no new TRIGGER_MERGED entry either.
    ev2 = _anomaly({"assetId": "CHILLER-8821", "customerId": "CUST-1042"})
    ev2["detail"].pop("caseId")
    handle_event(ev2, specialist_invoker=_Invoker())
    assert _types(case_id).count("TRIGGER_MERGED") == 1


def test_closed_case_does_not_block_a_new_one(store):
    _case_in("CLOSED")
    ev = _anomaly({"assetId": "CHILLER-8821", "customerId": "CUST-1042"})
    ev["detail"].pop("caseId")
    res = handle_event(ev, specialist_invoker=_Invoker())
    assert res.case_id != "SR-T1"
    assert len(_metas()) == 2


def test_find_open_case_for_asset_returns_newest_open(store):
    assert case_store.find_open_case_for_asset("CHILLER-8821") is None
    _case_in("CANCELLED", "SR-OLD")
    assert case_store.find_open_case_for_asset("CHILLER-8821") is None
    _case_in("SCHEDULED", "SR-LIVE")
    assert case_store.find_open_case_for_asset("CHILLER-8821") == "SR-LIVE"
