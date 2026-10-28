"""Ops Console seed baseline (console_api/scenario_seed.py): Case Information fields + realism fixes."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest

from opsresolve.common.state_machine import replay
from opsresolve.console_api import scenario_seed as seed

NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
DETAIL_KEYS = ("category", "subcategory", "assignedQueue", "caseOwner", "customerDescription", "sourceChannel")
TERMINAL = ("CLOSED", "CANCELLED")


@pytest.fixture(scope="module")
def s1():
    return seed.build_baseline(NOW, seed.DEFAULT_TENANT)


@pytest.fixture(scope="module")
def s2():
    return seed.build_baseline(NOW, seed.S2_TENANT)


@pytest.fixture(scope="module")
def baseline(s1, s2):
    """Both tenants' items together (case ids are unique across tenants)."""
    return {"cases": s1["cases"] + s2["cases"], "approvals": s1["approvals"] + s2["approvals"]}


def _metas(baseline):
    return {i["caseId"]: i for i in baseline["cases"] if i["sk"] == "META"}


def _case_items(baseline, cid):
    return [i for i in baseline["cases"] if i["pk"] == f"CASE#{cid}"]


def test_counts(s1, s2):
    one, two = seed.summarize(s1), seed.summarize(s2)
    assert (one["cases"], one["approvals"], one["pending"]) == (6, 5, 2)
    assert (two["cases"], two["approvals"], two["pending"]) == (4, 1, 1)


def test_every_item_has_its_tenant_and_no_case_is_in_both(s1, s2):
    for tenant, part in ((seed.DEFAULT_TENANT, s1), (seed.S2_TENANT, s2)):
        assert all(i["tenantId"] == tenant for i in part["cases"] + part["approvals"])
    assert not set(_metas(s1)) & set(_metas(s2))
    assert set(_metas(s2)) == seed.S2_CASE_IDS


def test_s2_baseline_has_no_open_case_for_the_demo_customer(s2):
    assert not [m for m in _metas(s2).values() if m["customerId"] == "CUST-2201"]
    (a,) = s2["approvals"]
    assert a["action"] == "SERVICE_CREDIT_OVER_CAP:60" and a["approverRole"] == "CARE_TEAM_LEAD"
    assert a["status"] == "PENDING" and _metas(s2)[a["caseId"]]["state"] == "CLOSED"


def test_demo_rule_one_active_case_per_asset_and_none_on_chiller_8821(s1):
    baseline = s1
    metas = _metas(baseline).values()
    active = [m for m in metas if m["state"] not in TERMINAL]
    assert sorted(m["assetId"] for m in active) == ["AHU-9011", "CHILLER-8822", "MRI-4410"]
    assert not any(m["state"] in ("OPEN", "CONTEXT_READY") for m in metas)  # the Open column is the demo case's
    assert Counter(m["state"] for m in metas if m["state"] in TERMINAL) == {"CLOSED": 2, "CANCELLED": 1}
    (on_8821,) = [m for m in metas if m["assetId"] == "CHILLER-8821"]
    assert on_8821["state"] == "CLOSED" and on_8821["caseId"] == seed.CLOSED_8821_ID
    assert not [i for i in _case_items(baseline, on_8821["caseId"]) if i["sk"].startswith("APPT#")]
    assert all(m["closedAt"] for m in metas if m["state"] in TERMINAL)
    assert {a["caseId"] for a in baseline["approvals"]} <= set(_metas(baseline))


def test_every_timeline_is_a_legal_state_sequence_ending_in_the_meta_state(baseline):
    for cid, meta in _metas(baseline).items():
        changes = sorted((i for i in _case_items(baseline, cid) if i.get("type") == "CASE_STATE_CHANGED"),
                         key=lambda i: i["sk"])
        seq = ["OPEN"] + [e["detail"]["to"] for e in changes]
        assert [e["detail"]["from"] for e in changes] == seq[:-1], cid
        assert replay(seq) and seq[-1] == meta["state"], cid
        assert meta["gsi1pk"] == f"STATE#{meta['state']}" and meta["gsi2pk"] == f"ASSET#{meta['assetId']}"


def test_every_meta_has_all_detail_keys(baseline):
    for cid, meta in _metas(baseline).items():
        for key in DETAIL_KEYS:
            assert meta.get(key), f"{cid} missing {key}"


def test_required_part_only_where_a_part_is_needed(baseline):
    with_part = {cid for cid, m in _metas(baseline).items() if "requiredPart" in m}
    assert with_part == {"SR-C7D4418A6E", "SR-91B6E3D07A", "SR-0B7D9C5E34", seed.CLOSED_COURIER_ID}
    part = _metas(baseline)["SR-91B6E3D07A"]["requiredPart"]
    assert set(part) == {"partId", "name", "qty", "unitValueUsd"} and part["partId"] == "ONT-X200"


def test_at_risk_case_owned_by_a_human_and_ont_technician(baseline):
    meta = _metas(baseline)["SR-91B6E3D07A"]
    assert meta["caseOwner"] != seed.ORCH_OWNER
    appts = [i for i in _case_items(baseline, "SR-91B6E3D07A") if i["sk"].startswith("APPT#")]
    assert [a["technicianId"] for a in appts] == ["TEL-804"]
    eta = [i for i in _case_items(baseline, "SR-91B6E3D07A") if i.get("type") == "TECHNICIAN_ETA_UPDATED"]
    assert eta[0]["detail"]["technicianId"] == "TEL-804"


def test_ahu_case_uses_ahu_certified_technician(baseline):
    appts = [i for i in _case_items(baseline, "SR-C7D4418A6E") if i["sk"].startswith("APPT#")]
    assert [a["technicianId"] for a in appts] == ["TECH-615"]


def test_customer_opened_case(baseline):
    meta = _metas(baseline)["SR-A2F15D8B60"]
    assert meta["sourceChannel"] == "WHATSAPP"
    items = _case_items(baseline, "SR-A2F15D8B60")
    first_in = min((i for i in items if i["sk"].startswith("MSG#") and i["direction"] == "INBOUND"),
                   key=lambda i: i["sk"])
    assert first_in["sentAt"] == meta["createdAt"]
    assert first_in["body"] == meta["customerDescription"]
    opened = next(i for i in items if i.get("type") == "CASE_OPENED")
    assert opened["detail"]["trigger"]["type"] == "CUSTOMER_REPLY"
    assert opened["detail"]["trigger"]["channel"] == "WHATSAPP"


def test_case_details_cover_exactly_the_baseline_cases(baseline):
    assert set(seed.CASE_DETAILS) == set(_metas(baseline))


def test_tech204_has_one_filler_job_away_from_the_demo_asset(baseline):
    appts = [i for i in baseline["cases"] if i["sk"].startswith("APPT#") and i["technicianId"] == "TECH-204"]
    assert [(a["pk"], a["status"]) for a in appts] == [("CASE#SR-FIELD-TECH204-01", "SCHEDULED")]
    (a,) = appts
    assert a["checklist"] and all(set(i) == {"item", "done"} and i["done"] is False for i in a["checklist"])
    assert a["manualRef"] and a["fieldBrief"]["text"]
    assert (a["slotStart"], a["slotEnd"]) == ("2026-10-12T14:00:00Z", "2026-10-12T16:00:00Z")  # NOW + 5 days
    meta = _metas(baseline)["SR-FIELD-TECH204-01"]
    assert meta["state"] == "SCHEDULED" and meta["assetId"] == "CHILLER-8822"


def test_original_work_order_ids_match_the_kit_seed():
    path = Path(__file__).resolve().parents[2] / "seed" / "s1-cold-chain-chiller" / "work_order_history.json"
    with open(path, encoding="utf-8") as f:
        ids = {row["workOrderId"] for row in json.load(f)}
    assert seed.ORIGINAL_WORK_ORDER_IDS == ids and len(ids) == 20
    path = Path(__file__).resolve().parents[2] / "seed" / "s2-broadband-whatsapp" / "work_order_history.json"
    with open(path, encoding="utf-8") as f:
        assert seed.ORIGINAL_WORK_ORDER_IDS_S2 == {row["workOrderId"] for row in json.load(f)}


def test_inventory_baselines():
    assert len(seed.INVENTORY_ON_HAND) == 5
    assert set(seed.INVENTORY_ON_HAND.values()) == {50}
    assert seed.INVENTORY_ON_HAND_S2 == {"INV#DEP-CHN-02#ONT-X200": 1, "INV#HUB-CHN-C1#ONT-X200": 5}
    assert all(pk.startswith("INV#") and pk.count("#") == 2
               for part in seed.INVENTORY_BY_TENANT.values() for pk in part)
