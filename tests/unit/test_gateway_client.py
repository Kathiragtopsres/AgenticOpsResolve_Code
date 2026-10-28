"""Fixed workload identity seeding for the Agent Invoker Lambda."""
import json
import os

import pytest

from opsresolve.agents import gateway_client


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    start = os.getcwd()
    monkeypatch.setattr(gateway_client, "_LAMBDA_WORKDIR", str(tmp_path))
    yield tmp_path
    os.chdir(start)


def test_writes_fixed_identity_in_lambda(workdir, monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "opsresolve-dev-agent-invoker")
    gateway_client._use_fixed_lambda_workload_identity()
    cfg = json.loads((workdir / ".agentcore.json").read_text(encoding="utf-8"))
    assert cfg == {
        "workload_identity_name": "opsresolve-dev-invoker",
        "user_id": "opsresolve-dev-invoker",
    }
    assert os.path.samefile(os.getcwd(), workdir)


def test_idempotent_keeps_existing_file(workdir, monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "opsresolve-dev-agent-invoker")
    (workdir / ".agentcore.json").write_text('{"workload_identity_name": "x", "user_id": "y"}')
    gateway_client._use_fixed_lambda_workload_identity()
    gateway_client._use_fixed_lambda_workload_identity()
    assert json.loads((workdir / ".agentcore.json").read_text())["workload_identity_name"] == "x"


def test_nothing_written_outside_lambda(workdir, monkeypatch):
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    cwd = os.getcwd()
    gateway_client._use_fixed_lambda_workload_identity()
    assert not (workdir / ".agentcore.json").exists()
    assert os.getcwd() == cwd
