"""AgentCore Gateway MCP client (Section 5.1 tool split, OPS-005+006).

Wires the 13 net-new write/external tools built by Lakshmi (OPS-007) behind AgentCore
Gateway -- one Lambda target, internal dispatch on tool_name -- as additional Strands
tools alongside the 10 local ones already resolved by ``tool_registry.py``. See
context.md's "Agent deployment model" section for the local/gateway tool split.

Gateway details and the OAuth2 access token are both fetched live at runtime, not from
``config/`` -- the AgentCore Runtime CodeZip only ships ``src/opsresolve/`` (narrowed
``codeLocation``, see the OPS-005+006 "console/ moved out" note in tasks.md), so
anything under the repo's ``config/`` directory never reaches the deployed agent:
  - Gateway URL: ``bedrock-agentcore-control`` ``GetGateway`` on the fixed
    ``GATEWAY_ID`` below (same pattern as every other hardcoded resource name in this
    repo, e.g. ``common/config.py``'s table names or ``foundation_stack.py``'s
    ``AGENTCORE_EXEC_ROLE_NAME`` -- an identifier, not a secret or environment-specific
    value, so it's fine as a constant).
  - Access token: an AgentCore Identity OAuth2 credential provider
    (``CREDENTIAL_PROVIDER_NAME``, registered once against the Gateway's Cognito
    client -- see resource.md's OPS-005+006 Gateway section for the registration
    command), fetched via the SDK's ``@requires_access_token`` (M2M client-credentials
    flow). Neither this module nor its caller ever sees the Cognito client secret --
    AgentCore Identity's token vault holds it.

``config/gateway.json`` and the ``gateway-client-secret`` entry in ``config/secrets.json``
are retired (no longer read anywhere) now that both pieces are fetched live.

Import-safe without a live Gateway/credential provider: every resolution function here
degrades to "no tools resolved" rather than raising, so ``build_agent()`` can still
build every specialist with just its local tools when the Gateway is unreachable, the
credential provider doesn't exist yet, or there's no live AWS session (unit tests, CI).
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from bedrock_agentcore.identity.auth import requires_access_token

from ..common.config import settings
from ..common.logging import get_logger

logger = get_logger(__name__)

# The Gateway's resource id (not a secret -- an AWS resource identifier, like a table
# or role name elsewhere in this repo). Built and owned by Lakshmi (OPS-007); confirmed
# live via `aws bedrock-agentcore-control list-gateways` this session. Update this if
# the Gateway is ever deleted/recreated under a new id.
GATEWAY_ID = "opsresolve-gateway-4pqnd9tdmj"

# AgentCore Identity OAuth2 credential provider wrapping the Gateway's Cognito
# client_credentials client (OPS-005+006 "Next up" fix). Created once via
# `aws bedrock-agentcore-control create-oauth2-credential-provider` -- see resource.md.
CREDENTIAL_PROVIDER_NAME = "opsresolve-dev-gateway-credentials"

# The OAuth2 scope the Gateway's Cognito app client is actually configured to grant
# (confirmed live via `aws cognito-idp describe-user-pool-client`) -- the resource
# server scope pattern Cognito uses for a client_credentials-only app client, not a
# generic OAuth scope we chose ourselves.
GATEWAY_OAUTH_SCOPES: tuple[str, ...] = ("Opsresolve-Gateway/genesis-gateway:invoke",)

# The 13 net-new tools behind the Gateway (tasks.md "Gateway tools" list). Distinct
# from tool_registry.LOCAL_TOOL_NAMES -- together the two lists cover every tool any
# SpecialistSpec declares.
GATEWAY_TOOL_NAMES: tuple[str, ...] = (
    "executeSafeRemoteAction",
    "generateServiceReport",
    "closeServiceCase",
    "retrieveTroubleshootingKnowledge",
    "findAndReservePart",
    "releasePartReservation",
    "transferPart",
    "proposeAppointmentSlots",
    "createServiceAppointment",
    "reassignTechnician",
    "updateWorkOrder",
    "sendCustomerMessage",
    "requestApproval",
    "applyServiceCredit",
    "dispatchCourier",
)

# The Gateway's own tool-naming scheme prefixes every tool with its Lambda target's
# name (confirmed live against the deployed Gateway: "Lambda___executeSafeRemoteAction",
# etc.) -- stripped back to the bare name above when wrapping for Strands, so
# SpecialistSpec.tools stays the single naming convention across local and gateway
# tools rather than callers needing to know the Gateway's internal prefix.
_GATEWAY_NAME_PREFIX = "Lambda___"

_GATEWAY_URL_CACHE: str | None = None  # cached GetGateway result, process lifetime
_MCP_CLIENT: Any = None  # cached strands.tools.mcp.MCPClient, started once per process
_GATEWAY_TOOLS_CACHE: list[Any] | None = None  # cached list_tools_sync() result


def _get_gateway_url() -> str | None:
    """``GetGateway`` against ``GATEWAY_ID``, cached for the process lifetime.

    No IAM change was needed for this: ``opsresolve-agentcore-agent-exe-role`` already
    has ``BedrockAgentCoreFullAccess`` attached, which grants ``bedrock-agentcore:*``
    on every resource in the account -- that covers ``GetGateway`` (control plane) and
    the token calls in ``_fetch_access_token`` below (data plane), both under the same
    ``bedrock-agentcore:`` IAM action prefix.
    """
    global _GATEWAY_URL_CACHE
    if _GATEWAY_URL_CACHE is not None:
        return _GATEWAY_URL_CACHE

    import boto3

    try:
        client = boto3.client("bedrock-agentcore-control", region_name=settings.region)
        _GATEWAY_URL_CACHE = client.get_gateway(gatewayIdentifier=GATEWAY_ID)["gatewayUrl"]
    except Exception as exc:
        logger.error(f"Could not fetch Gateway {GATEWAY_ID!r} details: {exc}")
        return None
    return _GATEWAY_URL_CACHE


# Fixed AgentCore Identity workload identity for the Agent Invoker Lambda. Without it the
# SDK's local-auth fallback (``.agentcore.json`` in the cwd) creates a new ``workload-*``
# identity on every cold start, because /var/task is read-only.
INVOKER_WORKLOAD_IDENTITY = "opsresolve-dev-invoker"
_LAMBDA_WORKDIR = "/tmp"  # the only writable path in Lambda


def _use_fixed_lambda_workload_identity() -> None:
    """In Lambda only: chdir to /tmp and seed ``.agentcore.json`` with the fixed identity.

    The AgentCore Runtime gets its workload token from the runtime itself, so this
    does nothing outside Lambda.
    """
    import os
    from pathlib import Path

    if not os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        return
    os.chdir(_LAMBDA_WORKDIR)
    config_path = Path(".agentcore.json")
    if not config_path.exists():
        config_path.write_text(
            json.dumps(
                {
                    "workload_identity_name": INVOKER_WORKLOAD_IDENTITY,
                    "user_id": INVOKER_WORKLOAD_IDENTITY,
                }
            ),
            encoding="utf-8",
        )


def _fetch_access_token() -> str | None:
    """OAuth2 access token for the Gateway via AgentCore Identity (M2M flow).

    Delegates entirely to the SDK's ``@requires_access_token`` decorator rather than
    talking to Cognito's token endpoint ourselves: it resolves the workload access
    token AgentCore Runtime already injects for this invocation (or bootstraps a local
    one for local dev/tests -- the SDK's own fallback, not something this module
    implements), exchanges it for a Gateway-scoped OAuth2 token via
    ``GetResourceOauth2Token``, and caches/refreshes it in AgentCore Identity's token
    vault -- this module and its caller never see the Cognito client secret.
    """
    try:
        _use_fixed_lambda_workload_identity()
        return _get_gateway_access_token()
    except Exception as exc:
        logger.error(
            f"Could not obtain a Gateway access token via AgentCore Identity credential "
            f"provider {CREDENTIAL_PROVIDER_NAME!r}: {exc}"
        )
        return None


@requires_access_token(
    provider_name=CREDENTIAL_PROVIDER_NAME,
    scopes=list(GATEWAY_OAUTH_SCOPES),
    auth_flow="M2M",
)
def _get_gateway_access_token(*, access_token: str) -> str:
    return access_token


def _get_mcp_client() -> Any:
    """Build (once) and return a started ``MCPClient`` for the Gateway, or ``None``.

    Cached for the process lifetime -- Strands' ``MCPClient`` runs a background thread
    and is meant to be reused across tool calls, not reopened per call (per its own
    docstring). A failed connection is not retried within the same process (mirrors
    ``registry.build_specialist_agents``'s per-process caching elsewhere in this
    package); restart the process to pick up a newly-live Gateway.
    """
    global _MCP_CLIENT
    if _MCP_CLIENT is not None:
        return _MCP_CLIENT

    gateway_url = _get_gateway_url()
    if not gateway_url:
        return None

    token = _fetch_access_token()
    if not token:
        logger.error(
            f"No Gateway access token available (credential provider "
            f"{CREDENTIAL_PROVIDER_NAME!r} unreachable, not yet created, or no live AWS "
            "session)"
        )
        return None

    from strands.tools.mcp import MCPClient

    # Use MCPClient's own url=/headers= constructor path rather than importing
    # mcp.client.streamable_http directly: that module's public API differs between
    # mcp 1.x (streamablehttp_client) and 2.x (streamable_http_client, different
    # signature) -- strands-agents (already a pinned dependency) carries its own
    # compat shim for exactly this and picks the right one internally.
    client = MCPClient(url=gateway_url, headers={"Authorization": f"Bearer {token}"})
    try:
        client.start()
    except Exception as exc:  # MCPClientInitializationError, or anything _transport raised
        logger.error(f"Could not connect to AgentCore Gateway: {exc}")
        return None

    _MCP_CLIENT = client
    return _MCP_CLIENT


def _list_gateway_tools() -> list[Any]:
    """Cached ``list_tools_sync()`` -- the Gateway's tool set is static for the
    process lifetime, so this avoids one MCP round trip per specialist built."""
    global _GATEWAY_TOOLS_CACHE
    if _GATEWAY_TOOLS_CACHE is not None:
        return _GATEWAY_TOOLS_CACHE

    client = _get_mcp_client()
    if client is None:
        return []
    try:
        _GATEWAY_TOOLS_CACHE = list(client.list_tools_sync())
    except Exception as exc:
        logger.error(f"Could not list Gateway tools: {exc}")
        return []
    return _GATEWAY_TOOLS_CACHE


def _error(code: str, message: str, arguments: dict, *, retryable: bool) -> dict:
    return {
        "error": {
            "code": code,
            "retryable": retryable,
            "message": message,
            "details": {},
            "correlationId": arguments.get("correlationId", ""),
        }
    }


def call_gateway_tool(name: str, arguments: dict) -> dict:
    """Call one Gateway tool with exact arguments (the planner's DIRECT goals, DD3).

    Returns the tool's parsed JSON result (a Lambda ``{"statusCode", "body"}`` envelope is
    unwrapped), or a tool error envelope when the Gateway is unreachable, the MCP call
    reports an error, or the result is not JSON. Never raises.
    """
    client = _get_mcp_client()
    if client is None:
        return _error("DOWNSTREAM_TIMEOUT", "Gateway unavailable", arguments, retryable=True)
    try:
        result = client.call_tool_sync(
            tool_use_id=f"direct-{uuid.uuid4().hex}",
            name=_GATEWAY_NAME_PREFIX + name,
            arguments=arguments,
        )
    except Exception as exc:
        logger.error(f"Gateway call {name} failed: {exc}")
        return _error("DOWNSTREAM_TIMEOUT", f"Gateway call failed: {exc}", arguments, retryable=True)

    # Transport-level failures use DOWNSTREAM_TIMEOUT (the only standard downstream code,
    # common.enums.ToolErrorCode); a tool's own error envelope comes back as parsed JSON.
    content = (result or {}).get("content") or []
    text = next((c.get("text") for c in content if isinstance(c, dict) and c.get("text") is not None), None)
    if (result or {}).get("status") == "error":
        return _error("DOWNSTREAM_TIMEOUT", text or f"Gateway tool {name} returned an error", arguments, retryable=True)
    try:
        parsed = json.loads(text) if text is not None else None
        if isinstance(parsed, dict) and isinstance(parsed.get("body"), str):
            parsed = json.loads(parsed["body"])
    except (TypeError, ValueError):
        parsed = None
    if not isinstance(parsed, dict):
        return _error("DOWNSTREAM_TIMEOUT", f"Gateway tool {name} returned a non-JSON result", arguments, retryable=True)
    return parsed


def resolve_gateway_tools(tool_names: list[str]) -> tuple[list[Any], list[str]]:
    """Resolve a subset of ``GATEWAY_TOOL_NAMES`` to Strands tools via the live Gateway.

    Returns ``(tools, unresolved)`` -- ``unresolved`` covers both names not on the
    Gateway at all and names that are on the Gateway but couldn't be reached right now
    (Gateway down, no token). Callers (``base.py``'s ``build_agent``) treat unresolved
    tools the same way ``tool_registry.missing_gateway_tools``'s output already was:
    logged and omitted, not a hard error, so a specialist still builds with whatever
    tools *are* available.
    """
    from strands.tools.mcp.mcp_agent_tool import MCPAgentTool

    unresolved: list[str] = [n for n in tool_names if n not in GATEWAY_TOOL_NAMES]
    wanted = [n for n in tool_names if n in GATEWAY_TOOL_NAMES]
    if not wanted:
        return [], unresolved

    available = _list_gateway_tools()
    by_bare_name = {t.tool_name.removeprefix(_GATEWAY_NAME_PREFIX): t for t in available}

    resolved: list[Any] = []
    for name in wanted:
        original = by_bare_name.get(name)
        if original is None:
            unresolved.append(name)
            continue
        # Rewrap with the bare name so the Strands tool's exposed name matches
        # SpecialistSpec.tools' own naming (the raw Gateway tool is named
        # "Lambda___<name>" -- see _GATEWAY_NAME_PREFIX).
        resolved.append(MCPAgentTool(original.mcp_tool, original.mcp_client, name_override=name))
    return resolved, unresolved
