"""POST /approvals/{id}/decision is tenant-scoped (console_api/approval_decision_handler.py)."""
from __future__ import annotations

import json

import boto3
import pytest
from moto import mock_aws

from opsresolve.console_api import _common as c
from opsresolve.console_api import approval_decision_handler as h

S = c.settings


def _create(client, name, keys):
    client.create_table(
        TableName=name, BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": k, "KeyType": t} for k, t in zip(keys, ("HASH", "RANGE"))],
        AttributeDefinitions=[{"AttributeName": k, "AttributeType": "S"} for k in keys])


class _Bus:
    def __init__(self):
        self.calls = []

    def put_events(self, Entries):
        self.calls.append(Entries)
        return {"FailedEntryCount": 0}


@pytest.fixture
def env(monkeypatch):
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN"):
        monkeypatch.setenv(k, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", S.region)
    with mock_aws():
        client = boto3.client("dynamodb", region_name=S.region)
        ddb = boto3.resource("dynamodb", region_name=S.region)
        _create(client, c.CASES_TABLE, ("pk", "sk"))
        _create(client, c.APPROVALS_TABLE, ("approvalId",))
        # SR-S2 is a broadband case; SR-S1 has no tenantId (= meravix-foods)
        ddb.Table(c.CASES_TABLE).put_item(Item={"pk": "CASE#SR-S2", "sk": "META", "tenantId": "meravix-broadband"})
        ddb.Table(c.CASES_TABLE).put_item(Item={"pk": "CASE#SR-S1", "sk": "META"})
        for aid, case in (("APR-S2", "SR-S2"), ("APR-S1", "SR-S1")):
            ddb.Table(c.APPROVALS_TABLE).put_item(Item={"approvalId": aid, "caseId": case, "status": "PENDING"})
        bus = _Bus()
        monkeypatch.setattr(h, "_ddb", lambda: ddb)
        monkeypatch.setattr(h, "_events", lambda: bus)
        yield {"ddb": ddb, "bus": bus}


def _decide(approval_id: str, tenant: str | None):
    claims = {"cognito:username": "lead"} | ({"custom:tenantId": tenant} if tenant else {})
    resp = h.handler({"pathParameters": {"id": approval_id}, "body": json.dumps({"decision": "APPROVE"}),
                      "requestContext": {"authorizer": {"jwt": {"claims": claims}}}})
    return resp["statusCode"], json.loads(resp["body"])


def test_another_tenants_approval_is_404_and_stays_pending(env):
    status, body = _decide("APR-S2", "meravix-foods")
    assert status == 404 and body["error"]["code"] == "NOT_FOUND"
    assert env["ddb"].Table(c.APPROVALS_TABLE).get_item(Key={"approvalId": "APR-S2"})["Item"]["status"] == "PENDING"
    assert env["bus"].calls == []


def test_own_tenants_approval_is_decided_and_published(env):
    assert _decide("APR-S2", "meravix-broadband")[0] == 200
    assert _decide("APR-S1", "meravix-foods")[0] == 200  # a case without tenantId belongs to meravix-foods
    assert len(env["bus"].calls) == 2


def test_no_tenant_claim_is_403(env):
    status, body = _decide("APR-S1", None)
    assert status == 403 and body["error"]["code"] == "FORBIDDEN"
