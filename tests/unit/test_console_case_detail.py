"""GET /cases/{id} read-time joins (console_api/read_handler.py) against moto DynamoDB.

Tables: the case store (GSI1 + GSI2, projection ALL), approvals (hash approvalId), and the
six mock tables the case detail joins. Mock items copy the live dev values (contact details
replaced with fictional ones) with MOCK_EXTRAS applied the same way the seed script does.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import boto3
import pytest
from moto import mock_aws

from opsresolve.common.config import settings
from opsresolve.console_api import _common as c
from opsresolve.console_api import read_handler
from opsresolve.console_api.mock_extras import MOCK_EXTRAS, missing_attributes
from opsresolve.console_api.scenario_seed import DEFAULT_TENANT, S2_CASE_IDS, S2_TENANT, build_baseline

NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)

OLD_TOP_KEYS = {"case", "correlationId", "plans", "appointments", "messages", "approvals"}
OLD_CASE_KEYS = {"caseId", "state", "column", "priority", "customerId", "customerName", "assetId", "title",
                 "channel", "createdAt", "closedAt", "restoreBy", "sla", "version"}
NEW_CASE_KEYS = {"category", "subcategory", "subcategoryTag", "assignedQueue", "caseOwner", "customerDescription",
                 "sourceChannel"}


def _m(pk: str, **attrs):
    return {"pk": pk, "sk": "META", **attrs}


MOCK_ITEMS = {
    settings.mock_customers_table: [
        _m("CUSTOMERS#CUST-1042", customerId="CUST-1042", name="Meridian Foods", segment="Food manufacturing"),
        _m("CUSTOMERS#CUST-2201", customerId="CUST-2201", name="Priya Nair (Nair Design Studio)",
           segment="Small business / home"),
        _m("CUSTOMERS#CUST-3107", customerId="CUST-3107", name="St. Aurelia General Hospital",
           segment="Healthcare provider"),
        _m("CUSTOMERS#CUST-2203", customerId="CUST-2203", name="Lakeside Dental Clinic", segment="Small business"),
    ],
    settings.mock_contacts_table: [
        _m("CONTACTS#CON-77", contactId="CON-77", customerId="CUST-1042", isPrimary=True, name="Anita Rao",
           role="Maintenance Manager", email="anita@example.test", phone="+12025550100", preferredChannel="RCS"),
        _m("CONTACTS#CON-78", contactId="CON-78", customerId="CUST-1042", isPrimary=False, name="Vikram Shah",
           role="Plant Manager", preferredChannel="SMS"),
        _m("CONTACTS#CON-2201", contactId="CON-2201", customerId="CUST-2201", isPrimary=True, name="Priya Nair",
           email="priya@example.test", phone="+12025550171", preferredChannel="WHATSAPP"),
        _m("CONTACTS#CON-2203", contactId="CON-2203", customerId="CUST-2203", isPrimary=True,
           name="Dr. Meera Pillai", role="Account holder", email="meera@example.test", preferredChannel="WHATSAPP"),
        _m("CONTACTS#CON-3107", contactId="CON-3107", isPrimary=True, name="Dr. Leena Joseph",
           role="Biomedical Engineering Lead", preferredChannel="RCS"),
        _m("CONTACTS#CON-3108", contactId="CON-3108", isPrimary=False, name="Radiology Operations Lead",
           role="Radiology Ops", preferredChannel="SMS"),
    ],
    settings.mock_sites_table: [
        _m("SITES#PLANT-A", siteId="PLANT-A", name="Plant A", address="12 Industrial Estate Road, Sector 4",
           accessNotes="Gate 2"),
        _m("SITES#PREM-2201", siteId="PREM-2201", address="Flat 3B, 44 Lake View Road", accessNotes="Buzzer 3B"),
        _m("SITES#RAD-01", siteId="RAD-01", name="Radiology Department, Block C", accessNotes="Escort required"),
        _m("SITES#PREM-2203", siteId="PREM-2203", name="Lakeside Dental Clinic", address="7 Greenways Road, RA Puram",
           accessNotes="Call on arrival"),
    ],
    settings.mock_assets_table: [
        _m("ASSETS#CHILLER-8821", assetId="CHILLER-8821", model="CX-500", serial="CX5-22-08821",
           installDate="2022-03-14", siteId="PLANT-A", criticality="HIGH"),
        _m("ASSETS#CHILLER-8822", assetId="CHILLER-8822", model="CX-500", serial="CX5-22-08822",
           installDate="2022-03-14", siteId="PLANT-A", criticality="HIGH"),
        _m("ASSETS#AHU-9011", assetId="AHU-9011", model="AHU-90", serial="AH9-21-09011", installDate="2021-11-02",
           siteId="PLANT-A", criticality="MEDIUM"),
        _m("ASSETS#MRI-4410", assetId="MRI-4410", model="MRX-3T", serial="MRX3T-4410", siteId="RAD-01",
           criticality="CRITICAL"),
        _m("ASSETS#ONT-55012", assetId="ONT-55012", model="ONT-X200", serial="ONTX-55012", siteId="PREM-2201"),
        _m("ASSETS#RTR-55013", assetId="RTR-55013", model="HGW-7", serial="HGW7-55013", siteId="PREM-2201"),
        _m("ASSETS#ONT-55031", assetId="ONT-55031", model="ONT-X200", serial="ONTX-55031", siteId="PREM-2203",
           displayName="ONT-X200 fibre ONT", installDate="2024-02-10", lastServiceDate="2024-02-10"),
    ],
    settings.mock_contracts_table: [
        _m("CONTRACTS#CTR-5510", contractId="CTR-5510", customerId="CUST-1042",
           assetIds=["CHILLER-8821", "CHILLER-8822", "AHU-9011"], coverage={"labour": True, "parts": True},
           restoreHours=4, slaTier="GOLD_4H", warrantyUntil="2027-03-13"),
        _m("CONTRACTS#CTR-UPT-3107", contractId="CTR-UPT-3107", customerId="CUST-3107",
           coverage={"afterHours": True, "labour": True, "parts": True}, restoreHours=8, slaTier="PREMIUM_UPTIME"),
        _m("CONTRACTS#PLAN-FIBRE-500-BIZ", contractId="PLAN-FIBRE-500-BIZ", customerId="CUST-2201",
           coverage={"courierReplacement": True, "hardware": True}, restoreHours=24, slaTier="BIZ_NEXT_DAY"),
        _m("CONTRACTS#PLAN-FIBRE-300-BIZ-2203", contractId="PLAN-FIBRE-300-BIZ-2203", customerId="CUST-2203",
           planName="Fibre 300 Business", coverage={"courierReplacement": True, "hardware": True}, restoreHours=24,
           slaTier="BIZ_NEXT_DAY", assetIds=["ONT-55031"], endDate="2027-01-31"),
    ],
    settings.mock_technicians_table: [
        _m("TECHNICIANS#TEL-804", technicianId="TEL-804", name="L. Fernandes"),
        _m("TECHNICIANS#TECH-615", technicianId="TECH-615", name="K. Das"),
        _m("TECHNICIANS#MED-118", technicianId="MED-118", name="S. George"),
        _m("TECHNICIANS#TECH-118", technicianId="TECH-118", name="D. Pillai"),
    ],
}


def _create(client, name, keys, gsis=None):
    kw = {}
    attrs = set(keys)
    if gsis:
        kw["GlobalSecondaryIndexes"] = [
            {"IndexName": n, "KeySchema": [{"AttributeName": h, "KeyType": "HASH"},
                                           {"AttributeName": r, "KeyType": "RANGE"}],
             "Projection": {"ProjectionType": "ALL"}} for n, h, r in gsis]
        attrs |= {a for _, h, r in gsis for a in (h, r)}
    client.create_table(
        TableName=name,
        KeySchema=[{"AttributeName": k, "KeyType": t} for k, t in zip(keys, ("HASH", "RANGE"))],
        AttributeDefinitions=[{"AttributeName": a, "AttributeType": "S"} for a in sorted(attrs)],
        BillingMode="PAY_PER_REQUEST", **kw)


@pytest.fixture
def env(monkeypatch):
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN"):
        monkeypatch.setenv(k, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", settings.region)
    monkeypatch.setattr(read_handler, "_now", lambda: NOW)
    read_handler._ddb.cache_clear()
    with mock_aws():
        client = boto3.client("dynamodb", region_name=settings.region)
        _create(client, c.CASES_TABLE, ("pk", "sk"), [("GSI1", "gsi1pk", "gsi1sk"), ("GSI2", "gsi2pk", "gsi2sk")])
        _create(client, c.APPROVALS_TABLE, ("approvalId",))
        ddb = boto3.resource("dynamodb", region_name=settings.region)
        for table, items in MOCK_ITEMS.items():
            _create(client, table, ("pk", "sk"))
            extras = MOCK_EXTRAS.get(table, {})
            with ddb.Table(table).batch_writer() as bw:
                for it in items:
                    bw.put_item(Item=it | missing_attributes(it, extras.get(it["pk"], {})))
        baselines = [build_baseline(NOW, t) for t in (DEFAULT_TENANT, S2_TENANT)]
        with ddb.Table(c.CASES_TABLE).batch_writer() as bw:
            for b in baselines:
                for it in b["cases"]:
                    bw.put_item(Item=it)
        with ddb.Table(c.APPROVALS_TABLE).batch_writer() as bw:
            for b in baselines:
                for it in b["approvals"]:
                    bw.put_item(Item=it)
        yield {"client": client, "ddb": ddb}
    read_handler._ddb.cache_clear()


def _ev(route: str, tenant: str = DEFAULT_TENANT, **kw) -> dict:
    claims = {"custom:tenantId": tenant} if tenant else {}
    return {"routeKey": route, "requestContext": {"authorizer": {"jwt": {"claims": claims}}}, **kw}


def _get(case_id, tenant=None):
    tenant = tenant or (S2_TENANT if case_id in S2_CASE_IDS else DEFAULT_TENANT)
    resp = read_handler.handler(_ev("GET /cases/{id}", tenant, pathParameters={"id": case_id}))
    return resp["statusCode"], json.loads(resp["body"])


def test_at_risk_broadband_case_full_detail(env):
    status, body = _get("SR-91B6E3D07A")
    assert status == 200
    assert OLD_TOP_KEYS <= set(body)
    assert OLD_CASE_KEYS | NEW_CASE_KEYS <= set(body["case"])
    case = body["case"]
    assert case["category"] == "Broadband" and case["sourceChannel"] == "IOT_TELEMETRY"
    assert case["caseOwner"] == "Rahul Mehta (Service Manager)"
    assert case["title"] == "Fibre ONT link flapping"

    cust = body["customer"]
    assert cust["customerId"] == "CUST-2203"
    assert cust["primaryContact"]["name"] == "Dr. Meera Pillai"
    assert cust["primaryContact"]["role"] == "Account holder"
    assert cust["primaryContact"]["email"] == "meera@example.test"
    assert cust["preferredChannel"] == "WHATSAPP"
    assert cust["serviceLocation"]["name"] == "Lakeside Dental Clinic"
    assert cust["serviceLocation"]["address"] == "7 Greenways Road, RA Puram"

    asset = body["asset"]
    assert asset["serialNumber"] == "ONTX-55031"
    assert asset["model"] == "ONT-X200" and asset["displayName"] == "ONT-X200 fibre ONT"
    assert asset["installedAt"] == "2024-02-10" and asset["lastServiceAt"] == "2024-02-10"
    assert asset["site"] == {"siteId": "PREM-2203", "name": "Lakeside Dental Clinic"}

    ent = body["entitlement"]
    assert ent["coverage"] == "Fibre 300 Business" and ent["coverageStatus"] == "ACTIVE"
    assert ent["partsCovered"] is True and ent["laborCovered"] is False
    assert ent["expiresAt"] == "2027-01-31" and ent["contractId"] == "PLAN-FIBRE-300-BIZ-2203"

    (appt,) = body["appointments"]
    assert {"technicianId", "slotStart", "slotEnd", "status", "workOrderId"} <= set(appt)
    assert appt["technicianName"] == "L. Fernandes" and appt["estimatedDurationMinutes"] == 120
    assert body["requiredPart"]["partId"] == "ONT-X200"
    assert body["approvals"] == [] and body["serviceCredit"] is None and body["resolutionPath"] is None


def test_last_service_uses_other_closed_case_on_the_asset(env):
    _, open_case = _get("SR-FIELD-TECH204-01")
    _, closed_case = _get("SR-0B7D9C5E34")
    assert open_case["asset"]["lastServiceAt"] == closed_case["case"]["closedAt"][:10]
    # The closed case does not count itself: falls back to the asset's lastServiceDate.
    assert closed_case["asset"]["lastServiceAt"] == "2025-11-04"


def test_hospital_contact_joins_after_customer_id_fix(env):
    _, body = _get("SR-5E20B84F19")
    assert body["customer"]["primaryContact"]["name"] == "Dr. Leena Joseph"
    assert body["entitlement"]["contractId"] == "CTR-UPT-3107"
    assert body["entitlement"]["laborCovered"] is True


def test_in_service_ahu_appointment(env):
    _, body = _get("SR-C7D4418A6E")
    assert body["case"]["state"] == "IN_SERVICE" and body["case"]["column"] == "In Service"
    (appt,) = body["appointments"]
    assert appt["technicianId"] == "TECH-615" and appt["status"] == "IN_PROGRESS"
    assert appt["estimatedDurationMinutes"] == 120


def test_case_without_field_visit_or_part(env):
    _, body = _get("SR-5E20B84F19")
    assert body["appointments"] == [] and body["requiredPart"] is None
    assert body["asset"]["installedAt"] == "2020-08-21"


def test_closed_chiller_8821_case(env):
    from opsresolve.console_api.scenario_seed import CLOSED_8821_ID
    _, body = _get(CLOSED_8821_ID)
    assert body["case"]["state"] == "CLOSED" and body["case"]["column"] == "Closed"
    assert body["case"]["title"] == "Chiller supply temperature trending high"
    assert body["entitlement"]["coverage"] == "Gold 4-hour restore"
    assert body["entitlement"]["expiresAt"] == "2027-03-13"
    assert body["appointments"] == []  # fixed remotely


def test_kpis_for_the_seed(env):
    resp = read_handler.handler(_ev("GET /kpis", queryStringParameters=None))
    k = json.loads(resp["body"])
    assert (k["mttr"]["minutes"], k["mttr"]["casesClosed"]) == (175.0, 2)
    assert k["slaBreachesAvoided"]["total"] == 2
    assert k["remoteResolutionRate"] == {"rate": 0.5, "remoteResolved": 1, "resolved": 2}
    assert k["csat"] == {"average": 4.5, "responses": 2}
    assert k["casesByChannel"] == {"WHATSAPP": 1, "SMS": 2, "EMAIL": 2, "RCS": 1}


def test_kpis_for_the_s2_seed(env):
    resp = read_handler.handler(_ev("GET /kpis", S2_TENANT, queryStringParameters=None))
    k = json.loads(resp["body"])
    assert (k["mttr"]["minutes"], k["mttr"]["casesClosed"]) == (385.0, 2)  # 110 + 660 min
    assert k["slaBreachesAvoided"]["total"] == 0
    assert k["remoteResolutionRate"] == {"rate": 0.5, "remoteResolved": 1, "resolved": 2}
    assert k["csat"] == {"average": 4.5, "responses": 2}
    assert k["casesByChannel"] == {"WHATSAPP": 4}


def test_closed_courier_case_shows_credit_and_resolution_path(env):
    _, body = _get("SR-3F8A62D1C4")
    assert body["resolutionPath"] == "COURIER_SELF_INSTALL"
    assert body["serviceCredit"] == {"status": "APPLIED", "amountUsd": 25, "downtimeHours": 5, "creditId": None,
                                     "approvalId": None}
    _, visit = _get("SR-6D2B90E7A1")
    assert visit["serviceCredit"]["status"] == "PENDING_APPROVAL" and visit["serviceCredit"]["amountUsd"] == 60
    assert [a["status"] for a in visit["approvals"]] == ["PENDING"]


def test_a_tenant_never_sees_the_other_tenants_data(env):
    for case_id, other in (("SR-91B6E3D07A", DEFAULT_TENANT), ("SR-5E20B84F19", S2_TENANT)):
        status, _ = _get(case_id, other)
        assert status == 404, case_id
        for route in ("GET /cases/{id}/timeline", "GET /cases/{id}/trace"):
            resp = read_handler.handler(_ev(route, other, pathParameters={"id": case_id}))
            assert resp["statusCode"] == 404, (route, case_id)

    def ids(route, tenant, key, **kw):
        body = json.loads(read_handler.handler(_ev(route, tenant, queryStringParameters=None, **kw))["body"])
        return body[key]

    s1_cases = {k["caseId"] for k in ids("GET /cases", DEFAULT_TENANT, "cases")}
    s2_cases = {k["caseId"] for k in ids("GET /cases", S2_TENANT, "cases")}
    assert len(s1_cases) == 6 and s2_cases == S2_CASE_IDS and not s1_cases & s2_cases
    for tenant, mine in ((DEFAULT_TENANT, s1_cases), (S2_TENANT, s2_cases)):
        assert {e["caseId"] for e in ids("GET /timeline", tenant, "events")} <= mine
        assert {a["caseId"] for a in ids("GET /approvals", tenant, "approvals")} <= mine
        assert {k["caseId"] for k in ids("GET /cases/names", tenant, "cases")} == mine
    assert json.loads(read_handler.handler(_ev("GET /cases/summary", S2_TENANT))["body"])["total"] == 4


def test_no_tenant_claim_is_403_and_tenant_route_describes_the_caller(env, monkeypatch):
    resp = read_handler.handler(_ev("GET /cases", None))
    assert resp["statusCode"] == 403 and json.loads(resp["body"])["error"]["code"] == "FORBIDDEN"
    monkeypatch.setattr(read_handler.tenants, "tenant", lambda t: {
        "tenantId": t, "displayName": "Meravix Broadband", "scenario": "S2",
        "scenarioButtons": ["customer-message"], "telemetryProfiles": [], "demoAssetId": "ONT-55012"})
    body = json.loads(read_handler.handler(_ev("GET /tenant", S2_TENANT))["body"])
    assert body == {"tenantId": S2_TENANT, "displayName": "Meravix Broadband", "scenario": "S2",
                    "scenarioButtons": ["customer-message"], "telemetryProfiles": [], "demoAssetId": "ONT-55012"}


def test_customer_opened_case_source_and_description(env):
    _, body = _get("SR-A2F15D8B60")
    assert body["case"]["sourceChannel"] == "WHATSAPP"
    assert body["case"]["customerDescription"].startswith("Hi, my internet has been down")


def test_descriptions_survive_masking_unchanged(env):
    from opsresolve.console_api.scenario_seed import CASE_DETAILS
    for cid, details in CASE_DETAILS.items():
        _, body = _get(cid)
        assert body["case"]["customerDescription"] == details["customerDescription"], cid


def test_unknown_asset_and_customer_degrade_to_null(env):
    env["ddb"].Table(c.CASES_TABLE).put_item(Item={
        "pk": "CASE#SR-ORPHAN0001", "sk": "META", "caseId": "SR-ORPHAN0001", "state": "OPEN",
        "customerId": "CUST-9999", "assetId": "NOPE-1", "createdAt": "2026-10-07T00:00:00Z",
        "gsi1pk": "STATE#OPEN", "gsi1sk": "P4#2026-10-07T00:00:00Z",
        "gsi2pk": "ASSET#NOPE-1", "gsi2sk": "2026-10-07T00:00:00Z"})
    status, body = _get("SR-ORPHAN0001")
    assert status == 200
    assert body["customer"] is None and body["asset"] is None and body["requiredPart"] is None
    assert body["entitlement"]["coverageStatus"] == "NOT_COVERED"
    assert all(v is None for k, v in body["entitlement"].items() if k != "coverageStatus")
    # No sourceChannel on META and no CASE_OPENED event -> null, not an error.
    assert body["case"]["sourceChannel"] is None and body["case"]["category"] is None


def test_failed_mock_joins_return_200_with_nulls(env):
    for table in (settings.mock_contacts_table, settings.mock_sites_table, settings.mock_assets_table,
                  settings.mock_contracts_table, settings.mock_technicians_table):
        env["client"].delete_table(TableName=table)  # every call now raises ResourceNotFoundException
    status, body = _get("SR-91B6E3D07A")
    assert status == 200
    assert body["customer"]["name"] == "Lakeside Dental Clinic"
    assert body["customer"]["primaryContact"] is None and body["customer"]["serviceLocation"] is None
    assert body["asset"] is None
    assert body["entitlement"]["coverageStatus"] == "UNKNOWN"  # the contracts lookup raised: not "NOT_COVERED"
    assert body["appointments"][0]["technicianName"] is None
    assert body["appointments"][0]["estimatedDurationMinutes"] == 120


def test_expired_contract(env, monkeypatch):
    monkeypatch.setattr(read_handler, "_now", lambda: datetime(2027, 2, 1, tzinfo=timezone.utc))
    _, body = _get("SR-91B6E3D07A")
    assert body["entitlement"]["coverageStatus"] == "EXPIRED"


def test_list_cards_do_not_gain_detail_keys(env):
    resp = read_handler.handler(_ev("GET /cases", queryStringParameters=None))
    cards = json.loads(resp["body"])["cases"]
    assert len(cards) == 6
    for card in cards:
        assert set(card) == OLD_CASE_KEYS


def test_field_app_part_request_and_visit_report_are_returned(env):
    cid = "SR-FIELD-TECH204-01"
    env["ddb"].Table(c.APPROVALS_TABLE).put_item(Item={
        "approvalId": "APR-FLD-0001", "caseId": cid, "action": "PART_REQUEST:CTRL-BRD-500", "amount": "420",
        "approverRole": "SERVICE_MANAGER", "status": "PENDING", "createdAt": "2026-10-07T11:50:00Z",
        "requestedBy": "TECH-204", "qty": 1, "note": "Board burnt", "source": "FIELD_APP"})
    report = {"checklist": [{"item": "Isolate power and apply lock-out", "done": True}], "debrief": "Board swapped.",
              "completedAt": "2026-10-07T11:55:00Z", "photoKeys": ["field/SR-FIELD-TECH204-01/p1.jpg"],
              "signatureKey": "field/SR-FIELD-TECH204-01/sig.png"}
    env["ddb"].Table(c.CASES_TABLE).update_item(
        Key={"pk": f"CASE#{cid}", "sk": "APPT#WO-88201"},
        UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in report),
        ExpressionAttributeNames={f"#{k}": k for k in report},
        ExpressionAttributeValues={f":{k}": v for k, v in report.items()})

    resp = read_handler.handler(_ev("GET /approvals", queryStringParameters={"status": "PENDING"}))
    row = next(a for a in json.loads(resp["body"])["approvals"] if a["approvalId"] == "APR-FLD-0001")
    assert (row["requestedBy"], row["qty"], row["note"], row["source"]) == ("TECH-204", 1, "Board burnt", "FIELD_APP")

    _, body = _get(cid)
    (appr,) = [a for a in body["approvals"] if a["approvalId"] == "APR-FLD-0001"]
    assert appr["source"] == "FIELD_APP" and appr["requestedBy"] == "TECH-204"
    (appt,) = body["appointments"]
    for key, value in report.items():
        assert appt[key] == value, key
