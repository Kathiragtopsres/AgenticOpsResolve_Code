"""Shared base for OpsResolve specialist agents.

Each specialist runs with its own system prompt (``prompts/<agent>.v<n>.md``), a tool
subset, and a Pydantic output schema (``schemas/``). Structured output is validated;
one automatic repair retry is attempted, then the case escalates (Section 5.1).

This module is framework-light so it imports without the Strands SDK installed
(useful for unit tests); wire the actual Strands ``Agent`` in ``build_agent``.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..common.enums import AutonomyLevel
from ..common.logging import get_logger, mask_payload

logger = get_logger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"

# config/ssm_parameters.json (repo-relative), mirrored to real SSM parameters at
# /opsresolve/<env>/models/* once foundation_stack.py provisions them for real (OPS-001
# TODO). Read directly from the checked-in file for now -- see config/ssm_parameters.json.
# Where the file lives depends on the packaging: <repo>/config (local, parents[3]) or
# <zip root>/config (Lambda /var/task/config and the AgentCore runtime zip, parents[2]).
_SSM_PARAMETERS_CANDIDATES = (
    Path(__file__).resolve().parents[3] / "config" / "ssm_parameters.json",
    Path(__file__).resolve().parents[2] / "config" / "ssm_parameters.json",
)
_SSM_PARAMETERS_PATH = _SSM_PARAMETERS_CANDIDATES[0]  # kept for the error message / old imports

# Default system prompt for specialists with no dedicated prompt file (deterministic
# agents like AssetContextAgent, or L1 agents like TechnicianMatchingAgent that haven't
# had a prompt authored yet). Keeps build_agent total for every SpecialistSpec in the
# registry rather than raising on the two that currently have prompt_file=None.
_FALLBACK_SYSTEM_PROMPT_TEMPLATE = (
    "You are the {name} for TCS Agentic OpsResolve.\n\n"
    "Purpose: {purpose}\n\n"
    "Use only the tools made available to you. Ground every claim in a tool result --"
    " never state a fact, cost, or time that didn't come from a tool call. If you lack"
    " the information or authority to proceed safely, say so rather than guessing."
)


def _load_model_ids() -> dict[str, str]:
    """Read model IDs from config/ssm_parameters.json (Section 5.1 model config).

    Falls back to an empty dict (build_agent then falls back to BedrockModel's own
    default) if the file is missing -- keeps this import-safe in environments (unit
    tests, CI) that don't have the repo's config/ directory checked out alongside src/.
    """
    path = next((p for p in _SSM_PARAMETERS_CANDIDATES if p.exists()), None)
    if path is None:
        logger.error(
            "Could not load ssm_parameters.json; looked in "
            f"{[str(p) for p in _SSM_PARAMETERS_CANDIDATES]}. Falling back to the Strands default model."
        )
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error(f"Could not load {path}: {exc}")
        return {}
    models: dict[str, str] = {}
    for key, value in raw.items():
        # "/opsresolve/demo/models/reasoning" -> "reasoning"
        if "/models/" in key:
            models[key.rsplit("/", 1)[-1]] = value
    return models


# Which named model each specialist uses (Section 5.1: "Primary reasoning model" for
# Orchestrator/Diagnostic/ServiceRecoveryAgent -- everything else on the lightweight
# model). Both keys currently resolve to the same Nova 2 Lite model id in
# config/ssm_parameters.json for this demo, but the split is kept so swapping either
# model independently later (e.g. a real Claude Sonnet/Haiku split) is a config change,
# not a code change.
REASONING_MODEL_AGENTS = frozenset(
    {"ServiceResolutionOrchestrator", "DiagnosticAgent", "ServiceRecoveryAgent"}
)


@dataclass
class SpecialistSpec:
    """Static description of a specialist agent (mirrors the Section 5.4 tables)."""

    name: str
    purpose: str
    tools: list[str] = field(default_factory=list)
    autonomy: AutonomyLevel = AutonomyLevel.L0
    prompt_file: str | None = None
    deterministic: bool = False

    def load_prompt(self) -> str | None:
        """Return the latest versioned prompt text, if a prompt file is configured."""
        if not self.prompt_file:
            return None
        path = PROMPTS_DIR / self.prompt_file
        return path.read_text(encoding="utf-8") if path.exists() else None

    def system_prompt(self) -> str:
        """Prompt text to hand the Strands Agent: the versioned prompt file if one
        exists, otherwise a generated fallback from name/purpose (Section 5.1 -- every
        specialist "runs with its own system prompt", but not every specialist has had
        one authored yet, e.g. TechnicianMatchingAgent).
        """
        return self.load_prompt() or _FALLBACK_SYSTEM_PROMPT_TEMPLATE.format(
            name=self.name, purpose=self.purpose
        )


def build_agent(spec: SpecialistSpec, model_id: str | None = None, tools: list | None = None):
    """Construct a Strands ``Agent`` for the given spec (Section 5.1).

    Wiring:
      - model: a Strands ``BedrockModel`` bound to ``model_id`` if given, otherwise the
        model configured for this specialist in config/ssm_parameters.json (reasoning
        model for Orchestrator/Diagnostic/ServiceRecovery, lightweight model for
        everyone else -- see REASONING_MODEL_AGENTS).
      - system_prompt: ``spec.system_prompt()`` (the versioned prompt file, or a
        generated fallback for specialists without one).
      - tools: the caller-supplied list if given (e.g. the orchestrator passing the 10
        specialist-agents-as-tools plus its own direct tools), otherwise resolved from
        ``spec.tools`` against the local tool registry (Gateway-only tool names are
        silently dropped until the Gateway client is wired -- see tool_registry.py).

    Deterministic specialists (``spec.deterministic=True``, e.g. AssetContextAgent) still
    get a real Strands ``Agent`` here -- "deterministic" describes the *specialist's own
    reasoning* (no free-form fault analysis, just tool calls), not that it skips the
    agent framework; its tightly-scoped prompt keeps it from doing more than assembling
    context.
    """
    from strands import Agent
    from strands.models import BedrockModel

    from .remote_actions import RemoteActionGate

    if tools is None:
        from .gateway_client import resolve_gateway_tools
        from .tool_registry import missing_gateway_tools, resolve_local_tools

        tools = resolve_local_tools(spec.tools)
        gateway_candidates = missing_gateway_tools(spec.tools)
        if gateway_candidates:
            gateway_tools, unresolved = resolve_gateway_tools(gateway_candidates)
            tools = [*tools, *gateway_tools]
            if unresolved:
                logger.info(
                    f"{spec.name}: tools {unresolved} could not be resolved against the "
                    "local registry or the AgentCore Gateway -- omitted from this Agent."
                )

    resolved_model_id = model_id
    if resolved_model_id is None:
        models = _load_model_ids()
        model_key = "reasoning" if spec.name in REASONING_MODEL_AGENTS else "lightweight"
        resolved_model_id = models.get(model_key)

    model = BedrockModel(model_id=resolved_model_id) if resolved_model_id else BedrockModel()

    recorder = ToolCallRecorder()
    agent = Agent(
        model=model,
        system_prompt=spec.system_prompt(),
        tools=tools,
        name=spec.name,
        description=spec.purpose,
        hooks=[RemoteActionGate(), ToolCallCap(), recorder],
    )
    agent.tool_recorder = recorder  # the orchestrator reads recorder.calls after each invocation
    return agent


# Size bound for one recorded tool input/output on the timeline (DynamoDB item limit is 400 KB).
TRACE_VALUE_LIMIT = 4000


def trace_value(value: Any) -> Any:
    """Masked, size-bounded copy of a tool input/output for the timeline (Trace Viewer)."""
    masked = mask_payload(value, max_str=1000, max_items=30)
    text = json.dumps(masked, default=str)
    if len(text) <= TRACE_VALUE_LIMIT:
        return masked
    return {"truncated": True, "preview": text[:TRACE_VALUE_LIMIT] + f"...(+{len(text) - TRACE_VALUE_LIMIT} chars)"}


def _is_error_output(output: Any) -> bool:
    return isinstance(output, dict) and bool(output.get("error"))


def tool_call_record(tool: str, tool_input: Any, output: Any, latency_ms: int | None, failed: bool = False) -> dict:
    """One tool call as stored in a SPECIALIST_DELEGATED event's ``detail.toolCalls``."""
    return {
        "tool": tool,
        "input": trace_value(tool_input),
        "output": trace_value(output),
        "latencyMs": latency_ms,
        "status": "ERROR" if failed or _is_error_output(output) else "OK",
    }


def _parse_tool_content(content: Any) -> Any:
    """Tool output from a Strands ToolResult ``content`` list: JSON text is parsed, other text kept."""
    values: list[Any] = []
    for block in content or []:
        if not isinstance(block, dict):
            continue
        if "json" in block:
            values.append(block["json"])
        elif "text" in block:
            try:
                values.append(json.loads(block["text"]))
            except (TypeError, ValueError):
                values.append(block["text"])
    if not values:
        return None
    return values[0] if len(values) == 1 else values


class ToolCallRecorder:
    """Strands hook provider: records every tool call of the current agent invocation.

    Each record is ``{tool, input, output, latencyMs, status}`` (masked and size-bounded).
    ``calls`` is reset at the start of every invocation; the orchestrator reads it right
    after the specialist returns and stores it on the SPECIALIST_DELEGATED timeline event,
    which is what the Ops Console Trace Viewer builds its tool spans from. Recording never
    raises into the agent loop.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._started: dict[str, float] = {}

    def reset(self) -> None:
        self.calls = []
        self._started = {}

    def register_hooks(self, registry, **_: Any) -> None:
        from strands.hooks import AfterToolCallEvent, BeforeInvocationEvent, BeforeToolCallEvent

        registry.add_callback(BeforeInvocationEvent, self._reset)
        registry.add_callback(BeforeToolCallEvent, self._start)
        registry.add_callback(AfterToolCallEvent, self._finish)

    def _reset(self, _event) -> None:
        self.reset()

    def _start(self, event) -> None:
        self._started[(event.tool_use or {}).get("toolUseId")] = time.perf_counter()

    def _finish(self, event) -> None:
        try:
            tool_use = event.tool_use or {}
            started = self._started.pop(tool_use.get("toolUseId"), None)
            if started is not None:
                latency_ms = int((time.perf_counter() - started) * 1000)
            else:
                duration = getattr(event, "duration", None)
                latency_ms = int(duration * 1000) if isinstance(duration, (int, float)) else None
            result = event.result
            failed = False
            if isinstance(result, dict):
                output = _parse_tool_content(result.get("content"))
                failed = result.get("status") == "error"
            else:  # an Exception instead of a ToolResult
                output = {"error": str(getattr(event, "exception", None) or result)}
                failed = True
            if output is None and failed:
                output = {"error": getattr(event, "cancel_message", None) or "tool call failed"}
            self.calls.append(tool_call_record(tool_use.get("name") or "unknown", tool_use.get("input"), output,
                                               latency_ms, failed))
        except Exception as exc:  # tracing must never break the agent
            logger.error(f"Tool call recording failed: {exc}")


# Hard cap on tool calls per agent invocation: a model stuck retrying a failing call
# (seen live on Nova 2 Lite: 321 identical sendCustomerMessage calls) stops fast.
MAX_TOOL_CALLS_PER_INVOCATION = 10


class ToolCallCap:
    """Strands hook provider: cancels the (cap+1)th tool call and ends the agent loop."""

    def __init__(self, cap: int = MAX_TOOL_CALLS_PER_INVOCATION):
        self.cap = cap
        self.calls = 0

    def register_hooks(self, registry, **_: Any) -> None:
        from strands.hooks import BeforeInvocationEvent, BeforeToolCallEvent

        registry.add_callback(BeforeInvocationEvent, self._reset)
        registry.add_callback(BeforeToolCallEvent, self._check)

    def _reset(self, _event) -> None:
        self.calls = 0

    def _check(self, event) -> None:
        self.calls += 1
        if self.calls <= self.cap:
            return
        name = (event.tool_use or {}).get("name")
        logger.error(f"Tool-call cap {self.cap} reached; cancelling {name} and stopping the agent")
        event.cancel_tool = (
            f"Tool-call limit ({self.cap}) reached. Do not call any more tools; "
            "report the outcome now."
        )
        event.invocation_state.setdefault("request_state", {})["stop_event_loop"] = True
