"""AgentCore Runtime packaging for ``opsresolve-agent`` (Section 5.1, OPS-005+006).

Wraps the whole agent -- Orchestrator + 10 specialists, all in-process
agents-as-tools (see context.md "Agent deployment model") -- as the single
``BedrockAgentCoreApp`` entrypoint that ``agentcore launch``/``deploy`` packages and
that the Agent Invoker Lambda will eventually call via ``InvokeAgentRuntime``.

Two invocation shapes are accepted by the one entrypoint:
  1. ``{"prompt": "..."}`` -- ad-hoc/test invocation. Runs the prompt straight through
     the Orchestrator's Strands ``Agent`` (agents-as-tools) and returns its response.
     Useful for ``agentcore invoke`` smoke tests and local ``agentcore configure
     --local`` runs without needing a real EventBridge event.
  2. A raw EventBridge event dict (``{"source", "detail-type", "detail": {...}}``) --
     the real integration path. Runs the deterministic control loop
     (``agents.orchestrator.handle_event``) exactly as the Agent Invoker Lambda does
     today locally (``runtime.handler``), just invoked here instead of from a Lambda.
     This is intentionally the *same* control loop, not a second one: AgentCore Runtime
     packaging doesn't change how a case is planned, delegated, or committed, only how
     the process is invoked.

Which shape a payload is gets decided by ``"prompt"`` vs ``"source"``/``"detail-type"``
keys -- see ``_looks_like_eventbridge_event``.
"""
from __future__ import annotations

from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp

# Absolute (not package-relative) imports: AgentCore Runtime executes this file directly
# as a script, so ``__package__`` is unset and ``from ..common import x`` raises
# "attempted relative import with no known parent package". Absolute imports resolve via
# the zip root (``/var/task``) being on sys.path with the ``opsresolve`` package intact --
# which is why the runtime's ``codeLocation`` is ``src/`` rather than ``src/opsresolve/``.
# These also work unchanged for local imports/tests, where ``src`` is on the path.
from opsresolve.agents import registry
from opsresolve.agents.base import build_agent
from opsresolve.agents.gateway_client import resolve_gateway_tools
from opsresolve.agents.orchestrator import SPEC as ORCHESTRATOR_SPEC
from opsresolve.agents.orchestrator import handle_event
from opsresolve.agents.tool_registry import missing_gateway_tools, resolve_local_tools
from opsresolve.common.logging import get_logger

logger = get_logger(__name__)

app = BedrockAgentCoreApp()

# Built lazily on first use (not at import time) so this module stays importable
# without a live Bedrock model/credentials -- e.g. for `python -c "import ..."` sanity
# checks or a future test that only exercises _looks_like_eventbridge_event.
_orchestrator_agent: Any = None


def _get_orchestrator_agent() -> Any:
    """Build (once) the Orchestrator's Strands Agent with all 10 specialists exposed
    as tools, plus its own direct tools (Section 5.2 topology)."""
    global _orchestrator_agent
    if _orchestrator_agent is None:
        specialist_tools = registry.specialist_tools_for_orchestrator()
        direct_tools = resolve_local_tools(ORCHESTRATOR_SPEC.tools)
        # requestApproval is a Gateway tool (OPS-005+006 "Next up"), not a local one --
        # resolve it the same way base.build_agent() now does for every specialist,
        # since this call site builds the Orchestrator's own direct tools by hand
        # rather than going through build_agent's default tool-resolution path.
        gateway_tools, unresolved = resolve_gateway_tools(missing_gateway_tools(ORCHESTRATOR_SPEC.tools))
        if unresolved:
            logger.info(
                f"{ORCHESTRATOR_SPEC.name}: direct tools {unresolved} could not be "
                "resolved against the local registry or the AgentCore Gateway -- omitted."
            )
        _orchestrator_agent = build_agent(
            ORCHESTRATOR_SPEC, tools=[*specialist_tools, *direct_tools, *gateway_tools]
        )
    return _orchestrator_agent


def _looks_like_eventbridge_event(payload: dict) -> bool:
    """True if ``payload`` is a raw EventBridge event rather than an ad-hoc prompt.

    EventBridge's invocation shape always carries ``source`` and ``detail-type``
    (Section 5.5 / the 8 rules in resource.md); an ad-hoc test payload carries
    ``prompt`` instead. A payload with neither is treated as ad-hoc too (falls through
    to the prompt branch's default message) rather than raising, so a malformed test
    call fails inside the agent invocation (a normal, tracked error) instead of at the
    entrypoint boundary.
    """
    return "prompt" not in payload and ("source" in payload or "detail-type" in payload)


@app.entrypoint
def invoke(payload: dict) -> dict:
    """Single AgentCore Runtime entrypoint for ``opsresolve-agent``.

    Returns a JSON-serialisable dict in both branches: the ad-hoc branch wraps the
    Strands agent's text response, the EventBridge branch returns the same flat
    InvocationResult summary shape ``runtime.handler._summarize`` produces, so a caller
    switching between invoking the Lambda locally and invoking the Runtime agent
    directly sees the same result shape either way.
    """
    if _looks_like_eventbridge_event(payload):
        return _invoke_control_loop(payload)
    return _invoke_ad_hoc(payload)


def _invoke_ad_hoc(payload: dict) -> dict:
    prompt = payload.get("prompt", "Hello")
    agent = _get_orchestrator_agent()
    result = agent(prompt)
    return {"result": str(result)}


def _invoke_control_loop(event: dict) -> dict:
    detail = event.get("detail", event)
    case_id = detail.get("caseId")
    try:
        result = handle_event(event, case_id=case_id)
    except Exception as exc:
        # Mirrors runtime.handler's contract: errors that escape handle_event's own
        # fail-safe are infra-level (e.g. DynamoDB unreachable), not case-level -- let
        # them surface as an entrypoint-level failure rather than a silent no-op.
        logger.error(f"handle_event raised for caseId={case_id!r}: {exc}")
        raise
    return {
        "caseId": result.case_id,
        "correlationId": result.correlation_id,
        "fromState": result.from_state,
        "toState": result.to_state,
        "escalated": result.escalated,
        "escalationReason": result.escalation_reason,
        "committed": result.committed,
        "toolCallCount": len(result.tool_calls),
    }


if __name__ == "__main__":
    app.run()
