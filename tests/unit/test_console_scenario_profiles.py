"""GET /scenario/telemetry/profiles (console_api/scenario_handler.py)."""
from __future__ import annotations

import json

import pytest

from opsresolve.console_api import scenario_handler as h


def _claims(tenant: str | None) -> dict:
    return {"authorizer": {"jwt": {"claims": {"custom:tenantId": tenant} if tenant else {}}}}


def _event(method: str, path: str, body: dict | None = None, tenant: str | None = "meravix-foods") -> dict:
    ev = {"routeKey": f"{method} {path}", "rawPath": path,
          "requestContext": {"http": {"method": method}, **_claims(tenant)}}
    if body is not None:
        ev["body"] = json.dumps(body)
    return ev


def _call(ev):
    resp = h.handler(ev)
    return resp["statusCode"], json.loads(resp["body"])


_TENANT_META = {
    "meravix-foods": {"tenantId": "meravix-foods", "demoAssetId": "CHILLER-8821",
                      "scenarioButtons": ["telemetry", "technician-delay", "part-shortage", "complete-work-order"],
                      "telemetryProfiles": [{"id": i} for i in ("degrading", "post_reset_abnormal", "repaired", "normal")]},
    "meravix-broadband": {"tenantId": "meravix-broadband", "demoAssetId": "ONT-55012",
                          "scenarioButtons": ["customer-message", "part-shortage", "complete-work-order"],
                          "telemetryProfiles": []},
}


@pytest.fixture(autouse=True)
def tenant_table(monkeypatch):
    monkeypatch.setattr(h.tenants, "tenant", lambda t: _TENANT_META[t])


@pytest.fixture
def no_aws(monkeypatch):
    def boom():
        raise AssertionError("no AWS client may be built")
    monkeypatch.setattr(h, "_iot", boom)
    monkeypatch.setattr(h, "_ddb", boom)
    monkeypatch.setattr(h, "_events", boom)


def test_profiles_lists_every_accepted_profile(no_aws):
    status, body = _call(_event("GET", "/scenario/telemetry/profiles"))
    assert status == 200
    ids = [p["id"] for p in body["profiles"]]
    assert set(ids) == set(h.TELEMETRY_PROFILES) and len(ids) == 4 == body["count"]
    assert body["defaultProfileId"] == "degrading" == ids[0]
    degrading = body["profiles"][0]
    assert degrading["label"] == "Predictive failure — CX-500"
    assert degrading["assetId"] == "CHILLER-8821" and degrading["assetModel"] == "CX-500"
    assert degrading["opensCase"] is True and degrading["samples"] == 42
    assert degrading["streaming"] is True and degrading["durationSec"] == 410
    assert degrading["aliases"] == ["s1_chiller_degrading"]
    assert all(p["streaming"] is False and p["durationSec"] == 0 for p in body["profiles"][1:])
    for p in body["profiles"]:
        assert p["label"] and p["description"]


def test_profiles_route_without_request_context(no_aws):
    status, body = _call({"routeKey": "GET /scenario/telemetry/profiles", "requestContext": _claims("meravix-foods")})
    assert status == 200 and body["count"] == 4


def test_a_tenant_without_profiles_gets_none_and_cannot_post_telemetry(no_aws):
    status, body = _call(_event("GET", "/scenario/telemetry/profiles", tenant="meravix-broadband"))
    assert status == 200 and body["profiles"] == [] and body["defaultProfileId"] is None and body["count"] == 0
    status, body = _call(_event("POST", "/scenario/telemetry", {"profile": "degrading"}, tenant="meravix-broadband"))
    assert status == 400 and body["error"]["code"] == "NOT_AVAILABLE"  # the button is not enabled for the tenant


def test_no_tenant_claim_is_403(no_aws):
    for ev in (_event("GET", "/scenario/telemetry/profiles", tenant=None),
               _event("POST", "/scenario/reset", tenant=None)):
        status, body = _call(ev)
        assert status == 403 and body["error"]["code"] == "FORBIDDEN"


class _Iot:
    def __init__(self):
        self.published = []

    def publish(self, **kw):
        self.published.append(kw)


class _SiteWise:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def execute_action(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise self.fail
        return {"actionId": "a"}


class _Lambda:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def invoke(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise self.fail


def _client_error(code):
    from botocore.exceptions import ClientError
    return ClientError({"Error": {"Code": code, "Message": "x"}}, "Op")


@pytest.fixture
def stubs(monkeypatch):
    s = {"iot": _Iot(), "sw": _SiteWise(), "lam": _Lambda()}
    monkeypatch.setattr(h, "_iot", lambda: s["iot"])
    monkeypatch.setattr(h, "_sitewise", lambda: s["sw"])
    monkeypatch.setattr(h, "_lambda", lambda: s["lam"])
    monkeypatch.setattr(h, "_sleep", lambda seconds: s.setdefault("slept", []).append(seconds))
    return s


def test_every_profile_id_is_accepted_by_post_validation(no_aws, stubs):
    _, body = _call(_event("GET", "/scenario/telemetry/profiles"))
    for p in body["profiles"]:
        status, out = _call(_event("POST", "/scenario/telemetry", {"profile": p["id"]}))
        assert status == 200 and out["result"]["profile"] == p["id"]
    assert stubs["iot"].published  # stub only; nothing real is published


def test_degrading_series_shape():
    s = h.TELEMETRY_PROFILES["degrading"]
    assert len(s) == 42
    assert all(35 <= x["motorCurrentA"] <= 40 and x["alarm"] is None for x in s[:18])
    assert all(1.5 <= x["motorCurrentA"] <= 2.5 for x in s[18:])
    assert s[-1]["compressorPressureVar"] == 0.274 and s[-1]["alarm"] == "CMP_CTRL_PV"
    assert s[-1]["supplyTempC"] == 7.7


def test_degrading_post_starts_inference_and_one_async_worker(no_aws, stubs):
    status, out = _call(_event("POST", "/scenario/telemetry", {"profile": "degrading"}))
    assert status == 200 and out["ok"] is True
    assert out["result"] == {"profile": "degrading", "assetId": "CHILLER-8821", "published": 0,
                             "streaming": True, "samples": 42, "durationSec": 410}
    (start,) = stubs["sw"].calls
    assert start["targetResource"] == {"computationModelId": h.SITEWISE_MODEL_ID}
    assert start["resolveTo"] == {"assetId": h.SITEWISE_ASSET_ID}
    assert start["actionDefinitionId"] == h.SITEWISE_INFERENCE_ACTION_ID
    assert json.loads(start["actionPayload"]["stringValue"]) == {"inferenceMode": "START",
                                                                 "dataUploadFrequency": "PT5M"}
    (invoke,) = stubs["lam"].calls
    assert invoke["InvocationType"] == "Event"
    assert json.loads(invoke["Payload"]) == {"scenarioWorker": "telemetryStream", "profile": "degrading",
                                             "assetId": "CHILLER-8821"}
    assert stubs["iot"].published == []  # the worker publishes, not the API call


def test_degrading_rejects_other_assets_without_side_effects(no_aws, stubs):
    status, body = _call(_event("POST", "/scenario/telemetry", {"profile": "degrading", "assetId": "CHILLER-8822"}))
    assert status == 400 and body["error"]["code"] == "BAD_REQUEST"
    assert not stubs["sw"].calls and not stubs["lam"].calls


def test_degrading_inference_start_failure_is_502_and_nothing_streams(no_aws, stubs):
    stubs["sw"].fail = _client_error("AccessDeniedException")
    status, body = _call(_event("POST", "/scenario/telemetry", {"profile": "degrading"}))
    assert status == 502 and body["error"]["code"] == "INFERENCE_START_FAILED"
    assert not stubs["lam"].calls


def test_degrading_worker_invoke_failure_stops_inference(no_aws, stubs):
    stubs["lam"].fail = _client_error("AccessDeniedException")
    status, body = _call(_event("POST", "/scenario/telemetry", {"profile": "degrading"}))
    assert status == 502 and body["error"]["code"] == "STREAM_START_FAILED"
    modes = [json.loads(c["actionPayload"]["stringValue"])["inferenceMode"] for c in stubs["sw"].calls]
    assert modes == ["START", "STOP"]


class _Ctx:
    def __init__(self, remaining_sec):
        self.remaining_sec = remaining_sec

    def get_remaining_time_in_millis(self):
        return int(self.remaining_sec * 1000)


def _worker_event():
    return {"scenarioWorker": "telemetryStream", "profile": "degrading", "assetId": "CHILLER-8821"}


def test_worker_streams_every_reading_then_waits_then_stops(no_aws, stubs):
    out = h.handler(_worker_event(), _Ctx(900))
    assert out == {"worker": "telemetryStream", "outcome": "ok"}
    assert len(stubs["iot"].published) == 42
    assert all(p["topic"] == "opsresolve/telemetry/CHILLER-8821" for p in stubs["iot"].published)
    sleeps = stubs["slept"]
    assert sleeps[:-1] == [h.STREAM_INTERVAL_SEC] * 41 and sleeps[-1] == h.INFERENCE_GRACE_SEC
    (stop,) = stubs["sw"].calls
    assert json.loads(stop["actionPayload"]["stringValue"]) == {"inferenceMode": "STOP"}


def test_worker_with_too_short_timeout_publishes_nothing_but_stops_inference(no_aws, stubs):
    out = h.handler(_worker_event(), _Ctx(60))
    assert out["outcome"] == "timeout_too_short" and stubs["iot"].published == []
    assert len(stubs["sw"].calls) == 1


def test_worker_never_raises_and_still_stops_inference(no_aws, stubs):
    class _Boom(_Iot):
        def publish(self, **kw):
            raise RuntimeError("iot down")

    stubs["iot"] = _Boom()
    out = h.handler(_worker_event(), _Ctx(900))
    assert out["outcome"] == "error" and len(stubs["sw"].calls) == 1


def test_worker_stop_failure_is_swallowed(no_aws, stubs):
    stubs["sw"].fail = _client_error("ThrottlingException")
    assert h.handler(_worker_event(), _Ctx(900))["outcome"] == "ok"


def test_api_events_are_never_mistaken_for_the_worker(no_aws, stubs):
    status, _ = _call(_event("POST", "/scenario/telemetry", {"profile": "normal"}))
    assert status == 200 and len(stubs["iot"].published) == 3 and not stubs["sw"].calls


def test_other_get_routes_still_404(no_aws):
    status, body = _call(_event("GET", "/scenario/reset"))
    assert status == 404 and body["error"]["code"] == "NOT_FOUND"


def test_post_bogus_profile_is_400_without_iot(no_aws):
    status, body = _call(_event("POST", "/scenario/telemetry", {"profile": "bogus"}))
    assert status == 400 and body["error"]["code"] == "BAD_REQUEST"
