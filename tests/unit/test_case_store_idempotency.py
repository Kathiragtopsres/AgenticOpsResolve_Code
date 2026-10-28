"""Unit tests for common/case_store.py idempotency (OPS-005+006).

Mocks the idempotency DynamoDB table with moto (dev dependency, first use in this repo
-- pyproject.toml pins ``moto>=5.0``; the installed version is 5.1.21, which ships the
``moto.mock_aws`` context manager/decorator used below, replacing the older
per-service ``mock_dynamodb`` decorators from moto 4.x).

Covers: check_event_processed / mark_event_processed, check_idempotency /
store_idempotency_result, and consume_postback_nonce's replay behaviour (first call
True, replay False) -- all against a real (mocked) DynamoDB table with the pk/sk
composite-key schema documented in resource.md, matching the exact schema-drift
incident called out there.
"""
from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from opsresolve.common import case_store, dynamo
from opsresolve.common.config import settings


def _create_idempotency_table():
    client = boto3.client("dynamodb", region_name=settings.region)
    client.create_table(
        TableName=settings.idempotency_table,
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture
def idempotency_table():
    # dynamo._resource() is lru_cache'd (module-level, one boto3 DynamoDB resource for
    # the process). Clear it around each test's mock_aws() context so a resource built
    # inside one test's mock doesn't leak into the next (or into a real session).
    dynamo._resource.cache_clear()
    with mock_aws():
        _create_idempotency_table()
        yield
    dynamo._resource.cache_clear()


# --- check_idempotency / store_idempotency_result ---------------------------------------


def test_check_idempotency_returns_none_when_key_unseen(idempotency_table):
    assert case_store.check_idempotency("some-key") is None


def test_store_then_check_idempotency_roundtrips_response(idempotency_table):
    response = {"caseId": "SR-1", "status": "OK"}
    case_store.store_idempotency_result("some-key", response)
    assert case_store.check_idempotency("some-key") == response


def test_idempotency_keys_are_namespaced_under_IDEMP_prefix(idempotency_table):
    """store_idempotency_result writes pk=IDEMP#<key> -- verify directly against the
    table so a future refactor can't silently drop the namespacing (which is what keeps
    tool-call idempotency keys from colliding with raw eventIds -- see
    check_event_processed's docstring)."""
    case_store.store_idempotency_result("my-key", {"ok": True})
    table = dynamo.get_table(settings.idempotency_table)
    item = table.get_item(Key={"pk": "IDEMP#my-key", "sk": "META"}).get("Item")
    assert item is not None
    assert item["response"] == {"ok": True}


# --- check_event_processed / mark_event_processed ---------------------------------------


def test_check_event_processed_returns_none_when_event_unseen(idempotency_table):
    assert case_store.check_event_processed("evt-1") is None


def test_mark_then_check_event_processed_roundtrips_result(idempotency_table):
    result = {"caseId": "SR-1", "toState": "CONTEXT_READY", "committed": True}
    case_store.mark_event_processed("evt-1", result)
    assert case_store.check_event_processed("evt-1") == result


def test_event_processed_key_distinct_from_tool_call_idempotency_key(idempotency_table):
    """An eventId and a tool-call idempotency key with the same raw string value must
    not collide -- check_event_processed prefixes with EVT# on top of IDEMP#."""
    case_store.store_idempotency_result("shared-value", {"source": "tool-call"})
    case_store.mark_event_processed("shared-value", {"source": "event"})

    assert case_store.check_idempotency("shared-value") == {"source": "tool-call"}
    assert case_store.check_event_processed("shared-value") == {"source": "event"}


def test_mark_event_processed_stores_under_EVT_prefix(idempotency_table):
    case_store.mark_event_processed("evt-42", {"ok": True})
    table = dynamo.get_table(settings.idempotency_table)
    item = table.get_item(Key={"pk": "IDEMP#EVT#evt-42", "sk": "META"}).get("Item")
    assert item is not None
    assert item["response"] == {"ok": True}


# --- consume_postback_nonce replay behaviour ---------------------------------------------


def test_consume_postback_nonce_true_on_first_use(idempotency_table):
    assert case_store.consume_postback_nonce("nonce-1") is True


def test_consume_postback_nonce_false_on_replay(idempotency_table):
    assert case_store.consume_postback_nonce("nonce-1") is True
    assert case_store.consume_postback_nonce("nonce-1") is False


def test_consume_postback_nonce_independent_per_nonce(idempotency_table):
    assert case_store.consume_postback_nonce("nonce-a") is True
    assert case_store.consume_postback_nonce("nonce-b") is True
    # replay of the first is still blocked independently of the second succeeding
    assert case_store.consume_postback_nonce("nonce-a") is False


def test_store_idempotency_result_accepts_float_tool_results(idempotency_table):
    """Gateway results parsed from JSON carry floats (valueUsd: 420.0); DynamoDB rejects float,
    which escalated a live case. store_idempotency_result converts them to Decimal."""
    case_store.store_idempotency_result("float-key", {"valueUsd": 420.5, "nested": [{"x": 1.25}], "n": 3})
    got = case_store.check_idempotency("float-key")
    assert got["valueUsd"] == 420.5 and got["nested"][0]["x"] == 1.25 and got["n"] == 3
