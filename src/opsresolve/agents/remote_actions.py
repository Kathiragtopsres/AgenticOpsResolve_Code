"""Per-asset remote-action policy (regulated-asset gate).

The asset record in ``opsresolve-<env>-mock-assets`` says which remote actions may run without a
human (``safeActions``) and which only with an approved approval (``approvalRequiredActions``).
That decision is enforced in code, not in a prompt:

  - ``RemoteActionGate`` (a Strands hook on every agent) cancels any ``executeSafeRemoteAction`` call
    for an action the case's asset does not list as safe, whatever the model decided.
  - the orchestrator (``orchestrator._state_after_diagnosis``) raises the approval for an
    ``approvalRequiredActions`` action and runs it only after it is APPROVED (a DIRECT goal; the
    Gateway checks the approval again).

Lookups fail closed: an unreadable asset record allows nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..common import case_store, dynamo
from ..common.config import settings
from ..common.logging import get_logger

logger = get_logger(__name__)

EXECUTE_TOOL = "executeSafeRemoteAction"


@dataclass
class AssetActions:
    """Remote actions the asset record allows (mock-assets ``safeActions`` / ``approvalRequiredActions``)."""

    safe: frozenset[str]
    approval_required: frozenset[str]
    asset_class: str | None = None


def asset_actions(asset_id: str | None) -> AssetActions | None:
    """Remote-action policy of an asset, or None when the record cannot be read (callers fail closed)."""
    if not asset_id:
        return None
    try:
        row = dynamo.get_item(settings.mock_assets_table, f"ASSETS#{asset_id}")
    except Exception as exc:
        logger.error(f"Asset action lookup failed for {asset_id}: {exc}")
        return None
    if not row:
        return None
    return AssetActions(
        safe=frozenset(str(a) for a in row.get("safeActions") or []),
        approval_required=frozenset(str(a) for a in row.get("approvalRequiredActions") or []),
        asset_class=row.get("assetClass"),
    )


def prompt_block(asset_id: str | None) -> str:
    """Instruction text telling the DiagnosticAgent which remote actions it may run on this asset."""
    actions = asset_actions(asset_id)
    if actions is None:
        return ("Remote actions for this asset: unknown. Do NOT run or propose any remote action; "
                "recommend FIELD or ESCALATE.\n")
    text = (f"Remote actions for this asset (assetClass {actions.asset_class or 'STANDARD'}): "
            f"you MAY run only {sorted(actions.safe) or 'none'}.")
    if actions.approval_required:
        text += (f" {sorted(actions.approval_required)} need a human approval: PROPOSE them in your answer "
                 'as remoteAction {"action": "<name>", "status": "PROPOSED"} and do NOT run them.')
    return text + "\n"


def blocked_reason(case_id: str | None, asset_id: str | None, action: str) -> str | None:
    """Why this executeSafeRemoteAction call must not run, or None when the asset lists it as safe."""
    if not case_id or not asset_id or not action:
        return "caseId, assetId and action are required"
    try:
        case_asset = case_store.get_case_metadata(case_id).asset_id
    except Exception as exc:
        logger.error(f"Remote action gate could not read case {case_id}: {exc}")
        return f"case {case_id} could not be verified"
    if case_asset != asset_id:
        return f"asset {asset_id} is not the asset of case {case_id}"
    actions = asset_actions(asset_id)
    if actions is None:
        return f"the remote-action policy of asset {asset_id} could not be read"
    if action in actions.safe:
        return None
    if action in actions.approval_required:
        return f"{action} needs a human approval on asset {asset_id}"
    return f"{action} is not a safe remote action for asset {asset_id}"


class RemoteActionGate:
    """Strands hook provider: cancels ``executeSafeRemoteAction`` for an action the asset does not list as safe."""

    def register_hooks(self, registry, **_: Any) -> None:
        from strands.hooks import BeforeToolCallEvent

        registry.add_callback(BeforeToolCallEvent, self._check)

    def _check(self, event) -> None:
        tool_use = event.tool_use or {}
        if not str(tool_use.get("name") or "").endswith(EXECUTE_TOOL):
            return
        args = tool_use.get("input") or {}
        reason = blocked_reason(args.get("caseId"), args.get("assetId"), str(args.get("action") or ""))
        if reason is None:
            return
        logger.error(f"Remote action blocked: {reason}")
        event.cancel_tool = (
            f"Blocked: {reason}. Do not run this action. Propose it in your final answer instead "
            '(remoteAction {"action": "...", "status": "PROPOSED"}) and report the outcome.'
        )
