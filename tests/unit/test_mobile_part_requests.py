"""C1: mobile job detail part-request status (mobile_web_app/lambdas/field-read/.../field_read.py).

The mobile Lambda ships its own ``opsresolve`` package, so the fixture imports it in isolation and
restores the repo's ``opsresolve`` modules afterwards (other tests keep importing ``src/``).
"""
from __future__ import annotations

import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

REPO = Path(__file__).resolve().parents[2]
LAMBDAS = REPO / "mobile_web_app" / "lambdas"
MOBILE_DIR = LAMBDAS / "field-read"


def _is_ops(name: str) -> bool:
    return name == "opsresolve" or name.startswith("opsresolve.")


@pytest.fixture
def fr(monkeypatch):
    saved = {k: v for k, v in sys.modules.items() if _is_ops(k)}
    for k in saved:
        del sys.modules[k]
    sys.path.insert(0, str(MOBILE_DIR))
    try:
        import opsresolve.field_api.field_read as mod
        yield mod
    finally:
        for k in [k for k in sys.modules if _is_ops(k)]:
            del sys.modules[k]
        sys.modules.update(saved)
        sys.path.remove(str(MOBILE_DIR))


@pytest.fixture
def aws(monkeypatch, fr):
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN"):
        monkeypatch.setenv(k, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    fr._ddb.cache_clear()
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(TableName=fr.c.APPROVALS_TABLE,
                            KeySchema=[{"AttributeName": "approvalId", "KeyType": "HASH"}],
                            AttributeDefinitions=[{"AttributeName": "approvalId", "AttributeType": "S"}],
                            BillingMode="PAY_PER_REQUEST")
        yield {"client": client, "table": boto3.resource("dynamodb", region_name="us-east-1").Table(
            fr.c.APPROVALS_TABLE)}
    fr._ddb.cache_clear()


def test_part_requests_only_this_case_newest_first(fr, aws):
    t = aws["table"]
    t.put_item(Item={"approvalId": "A1", "caseId": "SR-1", "action": "PART_REQUEST:CTRL-BRD-500", "qty": 1,
                     "note": "burnt", "status": "APPROVED", "requestedBy": "TECH-204",
                     "createdAt": "2026-10-13T09:00:00Z", "decidedAt": "2026-10-13T09:10:00Z", "reason": "ok"})
    t.put_item(Item={"approvalId": "A2", "caseId": "SR-1", "action": "PART_REQUEST:PRS-SNS-55", "qty": 2,
                     "status": "PENDING", "requestedBy": "TECH-204", "createdAt": "2026-10-13T10:00:00Z"})
    t.put_item(Item={"approvalId": "A3", "caseId": "SR-1", "action": "PART_OVER_THRESHOLD:CTRL-BRD-900",
                     "status": "PENDING", "createdAt": "2026-10-13T11:00:00Z"})
    t.put_item(Item={"approvalId": "A4", "caseId": "SR-2", "action": "PART_REQUEST:ONT-X200",
                     "status": "PENDING", "createdAt": "2026-10-13T12:00:00Z"})
    out = fr._part_requests("SR-1")
    assert [r["approvalId"] for r in out] == ["A2", "A1"]
    assert out[1] == {"approvalId": "A1", "partId": "CTRL-BRD-500", "qty": 1, "note": "burnt", "status": "APPROVED",
                      "requestedBy": "TECH-204", "createdAt": "2026-10-13T09:00:00Z",
                      "decidedAt": "2026-10-13T09:10:00Z", "reason": "ok"}
    assert out[0]["partId"] == "PRS-SNS-55" and out[0]["decidedAt"] is None


def test_missing_approvals_table_degrades_to_empty(fr, aws):
    aws["client"].delete_table(TableName=fr.c.APPROVALS_TABLE)
    assert fr._part_requests("SR-1") == []
