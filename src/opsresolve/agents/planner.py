"""Pluggable planning step for the orchestrator control loop (Section 5.3 Plan/Act).

The Plan/Act steps are the one part of the control loop that is genuinely
model-driven (Strands Agent + agents-as-tools, Section 5.1/5.2). Everything around it
(locking, state-machine validation, atomic commit, fail-safe escalation) is
deterministic and must be testable without a live Bedrock call, so planning sits behind
a small ``Planner`` protocol:

  - ``RuleBasedPlanner``: a deterministic stand-in that encodes the two worked examples
    from Section 5.6 / the orchestrator prompt (telemetry anomaly -> diagnose ->
    remote-fix attempt; ETA checkpoint -> recovery risk check) plus a generic
    "assemble context first" rule, and the resume rules for approvals, work-order
    completion, CSAT and slot booking (NEXT SESSION A1-A4, B4). Good enough to drive the
    control loop end-to-end in tests and local runs before AgentCore Runtime is wired.
  - ``StrandsPlanner`` (not implemented here): would wrap a Strands ``Agent`` built via
    ``agents.base.build_agent(orchestrator.SPEC, ...)`` with the 10 specialists exposed
    as tools, returning the same ``Plan`` shape via ``agent.structured_output``.

Swapping planners is a one-line change in ``orchestrator.handle_event`` (the
``planner`` parameter); nothing else in the control loop needs to change.

Goal modes: ``AGENT`` (default) delegates to the specialist's Strands agent, which picks
its own tool calls; ``DIRECT`` calls the named Gateway tool(s) with exactly
``Goal.inputs`` (no LLM), for consent-bound actions whose arguments come from a verified
event (a slot tap, an approval decision) and whose structured result the loop branches on.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from ..common import tenants
from ..common.config import lifecycle
from ..common.enums import CaseState
from . import recovery
from .scheduling import SLOT_SEARCH_DAYS

# Sentinel resolved to the case customer's primary contactId before a goal runs
# (agents.direct_tools.resolve_inputs).
PRIMARY_CONTACT = "$primaryContactId"
# Sentinels for the technician-matching inputs, resolved from the case's asset the same way:
# the asset's model (mock-assets) and the skill that model's technicians need.
ASSET_MODEL = "$assetModel"
REQUIRED_SKILL = "$requiredSkill"
# Ranked technician ids from the TechnicianMatchingAgent step, in rank order.
RANKED_TECHNICIANS = "$rankedTechnicianIds"
# Region of the case's customer (mock-customers), and the visit length of the asset model (tenant model
# profile), both resolved like the sentinels above.
CUSTOMER_REGION = "$customerRegion"
JOB_DURATION_MIN = "$jobDurationMin"
# Last-resort region when neither the customer nor the tenant has one.
DEFAULT_REGION = "SOUTH"

CSAT_REQUEST_BODY = "Your service visit is complete. How did we do?"

# Synthetic detail-type the orchestrator plans with right after it moves a case into a state whose
# work starts at once (PLANNING_FIELD, RESOLVED_REMOTE); it never appears on the event bus.
STATE_ENTERED = "State Entered"

# Timer purposes the orchestrator schedules for the demo lifecycle (see orchestrator._on_entered).
REMOTE_FIX_CHECK = "REMOTE_FIX_CHECK"
VERIFY_WINDOW = "VERIFY_WINDOW"
CSAT_WAIT_CAP = "CSAT_WAIT_CAP"
CUSTOMER_REPLY_WAIT = "CUSTOMER_REPLY_WAIT"
_LIFECYCLE_TIMERS = (REMOTE_FIX_CHECK, VERIFY_WINDOW, CSAT_WAIT_CAP, CUSTOMER_REPLY_WAIT)

_APPROVED = "APPROVED"
_REJECTED = "REJECTED"
_PART_REQUEST = "PART_REQUEST:"
_PART_OVER_THRESHOLD = "PART_OVER_THRESHOLD:"
_SAFE_REMOTE_ACTION = "EXECUTE_SAFE_REMOTE_ACTION:"
_SERVICE_CREDIT_OVER_CAP = "SERVICE_CREDIT_OVER_CAP:"


@dataclass
class Goal:
    """One step of a plan: a specialist to delegate to and the tool calls expected."""

    specialist: str
    expected_tools: list[str] = field(default_factory=list)
    rationale: str = ""
    # Exact tool arguments. DIRECT: passed verbatim to the Gateway tool. AGENT: given to
    # the specialist as "use exactly these arguments".
    inputs: dict = field(default_factory=dict)
    mode: str = "AGENT"  # "AGENT" | "DIRECT"
    # A failed goal ends the plan: the goals after it depend on it (e.g. dispatch only after the part is confirmed).
    stop_on_error: bool = False


@dataclass
class Plan:
    """Ordered list of goals plus the target state transition (Section 5.4.1 output)."""

    version: int
    goals: list[Goal]
    target_state: str | None
    rationale: str
    prompt_version: str = "rule-based-v1"
    # Timeline event to append for this trigger: {"type", "summary", "detail"}.
    record: dict | None = None
    # One intermediate state committed before target_state (e.g. SCHEDULED -> IN_SERVICE
    # -> VERIFYING when a work order completes on a case never marked IN_SERVICE).
    via_state: str | None = None
    # Committed only when no goal outcome has status ERROR (e.g. SCHEDULED after a booking).
    success_state: str | None = None
    # "DIAGNOSIS": the state to move to next depends on the DiagnosticAgent's answer in this plan
    # (decided after the plan's own target state is committed). "ENTER": no decision, just start the
    # work of the state the plan lands in. In both cases the orchestrator then runs the entry plan of
    # PLANNING_FIELD / RESOLVED_REMOTE / AWAITING_CUSTOMER in the same invocation.
    decide: str | None = None
    # Case META attributes to set (csatScore, verifyWindowElapsedAt, ...); never state/version.
    set_fields: dict | None = None


class Planner(Protocol):
    def plan(self, envelope: dict, trigger: dict) -> Plan: ...


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_slot_option(option_id: str | None) -> tuple[str, str, str] | None:
    """Parse a BOOK_SLOT option id ``<technicianId>~<slotStart>~<slotEnd>`` (built by the
    Gateway's ``_options``). Returns None unless there are exactly 3 non-empty parts and
    both times are ISO-8601 with start < end."""
    parts = str(option_id or "").split("~")
    if len(parts) != 3 or not all(p.strip() for p in parts):
        return None
    technician_id, slot_start, slot_end = (p.strip() for p in parts)
    start, end = _parse_iso(slot_start), _parse_iso(slot_end)
    if start is None or end is None or start >= end:
        return None
    return technician_id, slot_start, slot_end


def agent_json(result: Any) -> dict | None:
    """The JSON object a specialist put in its answer (a reply may be wrapped in code fences), else None."""
    text = result.get("summary") if isinstance(result, dict) else None
    if not isinstance(text, str) or "{" not in text or "}" not in text:
        return None
    try:
        data = json.loads(text[text.index("{"): text.rindex("}") + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def diagnostic_question(envelope: dict) -> str:
    """The clarifying question the DiagnosticAgent asked in this plan's earlier step ('' if none)."""
    for prior in reversed(envelope.get("priorResults") or []):
        if prior.get("specialist") == "DiagnosticAgent":
            question = (agent_json(prior.get("result")) or {}).get("question")
            return " ".join(str(question).split())[:500] if isinstance(question, str) else ""
    return ""


def _minutes(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _csat_score(value) -> int | None:
    text = str(value if value is not None else "").strip()
    if len(text) == 1 and text in "12345":
        return int(text)
    return None


class RuleBasedPlanner:
    """Deterministic planner encoding the orchestrator prompt's worked examples.

    Not a substitute for the LLM-driven planner described in Section 5.1 — it exists so
    the control loop (locking, state transitions, commit, fail-safe) can be built and
    tested today, and swapped for a Strands-backed planner without touching the loop.
    """

    def __init__(self, now_fn: Callable[[], datetime] | None = None):
        self._now = now_fn or (lambda: datetime.now(timezone.utc))

    def plan(self, envelope: dict, trigger: dict) -> Plan:
        state = envelope.get("state", CaseState.OPEN.value)
        case_id = envelope.get("caseId")
        trigger_type = trigger.get("type")
        trigger_name = trigger.get("name")
        detail_type = trigger.get("detailType")
        payload = trigger.get("payload") or {}
        event_id = trigger.get("eventId")
        version = (envelope.get("plan") or {}).get("version", 0) + 1
        label = detail_type or trigger_type
        flags = envelope.get("lifecycle") or {}
        asset_id = (envelope.get("asset") or {}).get("assetId")

        def record_only(summary: str, event_type: str = "EVENT_RECORDED", detail: dict | None = None,
                        target: str | None = None) -> Plan:
            return Plan(
                version=version, goals=[], target_state=target, rationale=summary,
                record={"type": event_type, "summary": summary,
                        "detail": detail if detail is not None else {"detailType": detail_type, "triggerType": trigger_type}},
            )

        # (1) A3: an escalated case is a human's now -- record the event, never transition.
        if state == CaseState.ESCALATED_HUMAN.value:
            return record_only(f"Recorded {label} while ESCALATED_HUMAN; no transition")

        # (1b) The orchestrator just moved the case into `state` after a decision: start that state's work.
        if detail_type == STATE_ENTERED:
            if state == CaseState.RESOLVED_REMOTE.value:
                return Plan(
                    version=version, goals=[self._csat_goal(case_id, event_id)],
                    target_state=CaseState.VERIFYING.value,
                    rationale="Remote fix verified: request CSAT, verify the repair.",
                )
            if state == CaseState.AWAITING_CUSTOMER.value:
                return self._ask_customer(version, case_id, event_id, envelope)
            # PLANNING_FIELD and CONTEXT_READY fall through to their own rules below
            if state not in (CaseState.PLANNING_FIELD.value, CaseState.CONTEXT_READY.value):
                return record_only(f"Entered {state}; nothing to start")

        # (1c) Demo lifecycle timers (scheduled by the orchestrator when it enters REMOTE_FIX_IN_PROGRESS,
        # VERIFYING, AWAITING_CUSTOMER). Checked before the state rules: they apply to the state they were set for.
        if trigger_type == "TIMER" and trigger_name in _LIFECYCLE_TIMERS:
            return self._timer(version, state, case_id, trigger_name, payload, flags, record_only)

        # (2) Context assembly and diagnosis (unchanged).
        if state == CaseState.OPEN.value:
            return Plan(
                version=version,
                goals=[
                    Goal("AssetContextAgent", ["getAssetContext", "getServiceHistory"],
                         "Assemble customer/asset/contract context before anything else."),
                ],
                target_state=CaseState.CONTEXT_READY.value,
                rationale="New case: assemble context (Section 8.2 OPEN -> CONTEXT_READY).",
            )

        if state == CaseState.CONTEXT_READY.value:
            return Plan(
                version=version,
                goals=[
                    Goal("SlaEntitlementAgent", ["checkServiceEntitlement"],
                         "Determine coverage/SLA/priority before diagnosing."),
                    self._diagnose_goal(
                        "Isolate fault; attempt safe remote action if it is safe for this asset. If you cannot "
                        "decide without information only the customer has, you may ask the customer ONE question "
                        "set (at most 2 questions) with recommendedPath ASK_CUSTOMER."
                    ),
                ],
                target_state=CaseState.DIAGNOSING.value,
                rationale="Context ready: check entitlement, then diagnose (worked example 1).",
                decide="DIAGNOSIS",
            )

        # (3) Approval requested: the case waits; nothing to do until it is decided.
        if detail_type == "Approval Requested":
            return record_only(f"Recorded Approval Requested in {state}; waiting for the decision")

        # (4) A2: approval decided -> resume instead of escalating.
        if detail_type == "Approval Decided":
            return self._approval_decided(
                version, state, case_id, asset_id, payload, record_only,
                (envelope.get("customer") or {}).get("customerId"),
            )

        # (5) A1: work order completed -> ask for CSAT and verify.
        if detail_type == "Work Order Completed":
            if state in (CaseState.IN_SERVICE.value, CaseState.SCHEDULED.value):
                goal = self._csat_goal(case_id, event_id)
                return Plan(
                    version=version, goals=[goal], target_state=CaseState.VERIFYING.value,
                    rationale="Work order completed: request CSAT, verify the repair.",
                    via_state=CaseState.IN_SERVICE.value if state == CaseState.SCHEDULED.value else None,
                )
            return record_only(f"Recorded Work Order Completed in {state}; no transition")

        # (5b) A part pick failed after booking: tenants with a courier self-install offer the choice (S2);
        # for the others (and when no courier option exists) the case falls through to the escalation below.
        if detail_type == "Part Pick Failed" and self._recovery_enabled(envelope):
            if state != CaseState.SCHEDULED.value:
                return record_only(f"Recorded Part Pick Failed in {state}; no transition")
            offer = self._part_pick_failed(version, case_id, event_id)
            if offer is not None:
                return offer

        # (6) A4: customer taps and replies.
        if trigger_type == "POSTBACK":
            return self._postback(version, state, case_id, event_id, payload, flags, record_only, envelope)
        if trigger_type == "CUSTOMER_REPLY":
            return self._customer_reply(version, state, case_id, payload, flags, record_only)

        # (6b) Duplicate producers: an anomaly merged into a case already past context assembly
        # is recorded only (the demo timers decide the remote fix, never telemetry).
        if detail_type == "Telemetry Anomaly Detected":
            # No timeline entry: the case opener already writes (at most one per 15 min) a TRIGGER_MERGED
            # event, and a streaming producer sends dozens of these.
            return Plan(version=version, goals=[], target_state=None,
                        rationale=f"{label} in {state}: merged into the open case; no transition")

        # (7) Worked examples (unchanged) and field planning (B4).
        if state == CaseState.DIAGNOSING.value and trigger_type == "TELEMETRY":
            return Plan(
                version=version,
                goals=[
                    Goal("DiagnosticAgent", ["executeSafeRemoteAction"],
                         "Telemetry anomaly with a diagnosis already on record: attempt "
                         "the safe remote action if the allow-list covers it."),
                ],
                target_state=CaseState.REMOTE_FIX_IN_PROGRESS.value,
                rationale="Worked example 1: attempt safe remote action on telemetry anomaly.",
            )

        if state == CaseState.REMOTE_FIX_IN_PROGRESS.value:
            # The REMOTE_FIX_CHECK timer decides the outcome (rule 1c); anything else is just noted.
            return record_only(f"Recorded {label} while the remote fix is running; no transition")

        if trigger_type == "TIMER" and trigger_name == "ETA_CHECKPOINT":
            return Plan(
                version=version,
                goals=[
                    Goal("ServiceRecoveryAgent", ["getTechnicianStatus", "findQualifiedTechnicians"],
                         "Run the deterministic risk engine and rank recovery options if at risk."),
                ],
                target_state=None,
                rationale="Worked example 2: ETA checkpoint -> recovery risk check.",
            )

        if state == CaseState.PLANNING_FIELD.value:
            return Plan(
                version=version,
                goals=[
                    Goal("PartsAgent", ["findAndReservePart"], "Hold the likely part."),
                    *self._field_planning_goals(case_id),
                ],
                target_state=None,  # SCHEDULED only once the customer books
                rationale="Field visit required: reserve part, rank technicians, propose slots, offer them.",
            )

        # (8) Fallback: no rule matched. Let the orchestrator's fail-safe handle it rather
        # than guessing a transition (Section 5.6: "escalate rather than guess").
        return Plan(
            version=version,
            goals=[],
            target_state=CaseState.ESCALATED_HUMAN.value,
            rationale=(
                f"No planning rule for state={state}, trigger type={trigger_type}, "
                f"name={trigger_name}, detailType={detail_type}; escalating."
            ),
        )

    # --- rule helpers ----------------------------------------------------------------

    @staticmethod
    def field_work_goal() -> Goal:
        """Run once when the case enters PLANNING_FIELD, before the parts step: the part to bring and the
        technician's checklist, brief and manual reference (the orchestrator stores them on the case)."""
        return Goal(
            "DiagnosticAgent", ["retrieveTroubleshootingKnowledge"],
            "Plan the field work: a technician will visit this asset. Return suspectedPartId and fieldWork "
            "(checklist, brief, manualRef) from the knowledge base. Run no remote action; do not ask the customer.",
        )

    @staticmethod
    def _diagnose_goal(rationale: str) -> Goal:
        return Goal("DiagnosticAgent",
                    ["getAssetHealth", "retrieveTroubleshootingKnowledge", "executeSafeRemoteAction"], rationale)

    @staticmethod
    def _ask_customer(version, case_id, event_id, envelope) -> Plan:
        """Entry plan of AWAITING_CUSTOMER: send the DiagnosticAgent's question (CLARIFY, asked once per case)."""
        question = diagnostic_question(envelope)
        return Plan(
            version=version,
            goals=[Goal(
                "CommunicationAgent", ["sendCustomerMessage"],
                "Ask the customer the clarifying question from the diagnosis.",
                inputs={
                    "caseId": case_id,
                    "contactId": PRIMARY_CONTACT,
                    "intent": "CLARIFY",
                    "data": {"body": question},
                    "idempotencyKey": f"{case_id}:clarify",
                },
                mode="DIRECT",
            )],
            target_state=None,
            rationale="Diagnosis needs the customer's answer: ask once and wait.",
            record={"type": "CUSTOMER_QUESTION_ASKED", "summary": f"Asked the customer: {question}",
                    "detail": {"question": question}},
        )

    def _rediagnose(self, version, why: str, record: dict, rationale: str) -> Plan:
        """AWAITING_CUSTOMER -> DIAGNOSING: run the diagnosis again; the customer is never asked a second time."""
        return Plan(
            version=version,
            goals=[self._diagnose_goal(f"Isolate fault again. {why} Do NOT ask the customer again.")],
            target_state=CaseState.DIAGNOSING.value,
            rationale=rationale,
            record=record,
            decide="DIAGNOSIS",
        )

    def _timer(self, version, state, case_id, name, payload, flags, record_only) -> Plan:
        settings = lifecycle()

        def stale(expected: str) -> Plan | None:
            if state == expected:
                return None
            return record_only(f"Timer {name} fired while {state}; it no longer applies",
                               detail={"timer": name, "state": state})

        if name == REMOTE_FIX_CHECK:
            if (skip := stale(CaseState.REMOTE_FIX_IN_PROGRESS.value)):
                return skip
            action = str(payload.get("action") or "The remote action")
            wait = _minutes(settings.remote_fix_wait_minutes)
            if settings.remote_fix_outcome == "PASS":
                return Plan(
                    version=version, goals=[], target_state=CaseState.RESOLVED_REMOTE.value, decide="ENTER",
                    rationale=f"{action} held after {wait} min: resolved remotely.",
                    record={"type": "REMOTE_FIX_VERIFIED", "summary": f"{action} held after {wait} min; resolved remotely",
                            "detail": {"action": action, "waitMinutes": settings.remote_fix_wait_minutes}},
                )
            return Plan(
                version=version, goals=[], target_state=CaseState.PLANNING_FIELD.value, decide="ENTER",
                rationale=f"{action} did not hold after {wait} min: plan a field visit.",
                record={"type": "REMOTE_FIX_FAILED", "summary": f"{action} did not hold after {wait} min; field visit needed",
                        "detail": {"action": action, "waitMinutes": settings.remote_fix_wait_minutes}},
            )

        if name == VERIFY_WINDOW:
            if (skip := stale(CaseState.VERIFYING.value)):
                return skip
            score = flags.get("csatScore")
            window = _minutes(settings.verification_window_minutes)
            fields = {"verifyWindowElapsedAt": _iso_z(self._now())}
            if score is not None:
                return Plan(
                    version=version, goals=[], target_state=CaseState.CLOSED.value, set_fields=fields,
                    rationale=f"Verification window ({window} min) elapsed and CSAT {score}/5 is in: close.",
                    record={"type": "VERIFICATION_WINDOW_ELAPSED",
                            "summary": f"Verification window ({window} min) elapsed; CSAT {score}/5 on record; closing the case",
                            "detail": {"csatScore": score}},
                )
            return Plan(
                version=version, goals=[], target_state=None, set_fields=fields,
                rationale=f"Verification window ({window} min) elapsed; waiting for the customer's CSAT.",
                record={"type": "VERIFICATION_WINDOW_ELAPSED",
                        "summary": f"Verification window ({window} min) elapsed; waiting for the customer's CSAT",
                        "detail": {}},
            )

        if name == CSAT_WAIT_CAP:
            if (skip := stale(CaseState.VERIFYING.value)):
                return skip
            wait = _minutes(settings.csat_wait_minutes)
            if flags.get("csatScore") is None:
                return Plan(
                    version=version, goals=[], target_state=CaseState.CLOSED.value,
                    rationale=f"No CSAT within {wait} min: close without a score.",
                    record={"type": "CASE_CLOSED_NO_CSAT", "summary": f"No CSAT within {wait} min; closing the case without a score",
                            "detail": {"waitMinutes": settings.csat_wait_minutes}},
                )
            return Plan(
                version=version, goals=[], target_state=CaseState.CLOSED.value,
                rationale=f"CSAT wait cap ({wait} min) reached with a score on record: close.",
                record={"type": "VERIFICATION_WINDOW_ELAPSED", "summary": f"CSAT wait cap ({wait} min) reached; closing the case",
                        "detail": {"csatScore": flags.get("csatScore")}},
            )

        # CUSTOMER_REPLY_WAIT
        if (skip := stale(CaseState.AWAITING_CUSTOMER.value)):
            return skip
        wait = _minutes(settings.customer_reply_wait_hours)
        return self._rediagnose(
            version, f"The customer did not answer the question within {wait} h: decide with what you have.",
            {"type": "CUSTOMER_REPLY_TIMEOUT",
             "summary": f"Customer did not reply within {wait} h; re-running the diagnosis without their answer",
             "detail": {"waitHours": settings.customer_reply_wait_hours}},
            "No customer reply: re-run the diagnosis.",
        )

    @staticmethod
    def _csat_goal(case_id: str | None, event_id: str | None) -> Goal:
        return Goal(
            "CommunicationAgent", ["sendCustomerMessage"],
            "Visit complete: send the customer the CSAT request.",
            inputs={
                "caseId": case_id,
                "contactId": PRIMARY_CONTACT,
                "intent": "CSAT",
                "data": {"body": CSAT_REQUEST_BODY},
                "idempotencyKey": f"{case_id}:csat:{event_id}",
            },
            mode="DIRECT",
        )

    def _field_planning_goals(
        self, case_id: str | None, resend_key: str | None = None, start: datetime | None = None
    ) -> list[Goal]:
        """Technician ranking -> availability-based slots -> offer to the customer (B4).
        ``resend_key`` gives the offer message its own idempotency key (a re-offer must not hit the cached first send).
        ``start`` is where the slot search begins (default now)."""
        now = start or self._now()
        offer_inputs = {"caseId": case_id, "contactId": PRIMARY_CONTACT, "intent": "APPOINTMENT_OPTIONS"}
        if resend_key:
            offer_inputs["idempotencyKey"] = resend_key
        return [
            Goal(
                "TechnicianMatchingAgent", ["findQualifiedTechnicians"],
                "Rank technicians for the case's asset (use the tool arguments exactly as given).",
                inputs={
                    "caseId": case_id,
                    "assetModel": ASSET_MODEL,
                    "requiredSkill": REQUIRED_SKILL,
                    "region": CUSTOMER_REGION,
                    "windowStart": _iso_z(now),
                },
                mode="DIRECT",
            ),
            Goal(
                "SchedulingDispatchAgent", ["proposeAppointmentSlots"],
                "Offer slots from technician availability. technicianIds = the ranked technician ids "
                "from TechnicianMatchingAgent, best first.",
                inputs={
                    "caseId": case_id,
                    "technicianIds": RANKED_TECHNICIANS,
                    "windowStart": _iso_z(now),
                    "windowEnd": _iso_z(now + timedelta(days=SLOT_SEARCH_DAYS)),
                    "durationMin": JOB_DURATION_MIN,
                },
            ),
            Goal(
                "CommunicationAgent", ["sendCustomerMessage"],
                "Offer the appointment options: send data.slots = the slots returned by "
                "SchedulingDispatchAgent.",
                inputs=offer_inputs,
            ),
        ]

    @staticmethod
    def _recovery_enabled(envelope: dict) -> bool:
        tenant = tenants.tenant(envelope.get("tenantId"))
        return bool((tenant.get("recovery") or {}).get("courierSelfInstall"))

    @staticmethod
    def _part_pick_failed(version, case_id, event_id) -> Plan | None:
        """SCHEDULED -> AT_RISK and offer the customer the courier self-install or a technician tomorrow.
        None when the courier option cannot be offered."""
        if recovery.courier_plan(case_id) is None:
            return None
        options = [
            {"optionId": recovery.COURIER, "label": "Courier today", "intent": recovery.RECOVERY_CHOICE},
            {"optionId": recovery.TECH_TOMORROW, "label": "Technician tomorrow", "intent": recovery.RECOVERY_CHOICE},
        ]
        body = (
            "Sorry, the replacement fibre box we planned for your visit is not available at the depot. "
            "We can send it by courier today for you to install yourself with our guide, "
            "or book a technician for tomorrow."
        )
        return Plan(
            version=version,
            goals=[Goal(
                "CommunicationAgent", ["sendCustomerMessage"],
                "Offer the customer the courier self-install or a technician tomorrow.",
                inputs={
                    "caseId": case_id, "contactId": PRIMARY_CONTACT, "intent": "RECOVERY_OFFER",
                    "data": {"body": body, "options": options},
                    "idempotencyKey": f"{case_id}:recovery:{event_id}",
                },
                mode="DIRECT",
            )],
            target_state=CaseState.AT_RISK.value,
            rationale="The booked part could not be picked: at risk, offer the recovery options.",
            record={"type": "PART_PICK_FAILED", "summary": "Part pick failed; offered a courier self-install or a technician tomorrow",
                    "detail": {"options": [o["optionId"] for o in options]}},
            set_fields={"recoveryOptions": [o["optionId"] for o in options]},
        )

    def _recovery_choice(self, version, case_id, event_id, option_id, envelope, record_only) -> Plan:
        """The customer's tap on the recovery offer (state AT_RISK)."""
        appt = envelope.get("appointment") or {}
        work_order_id = appt.get("workOrderId") if appt.get("status") not in ("CANCELLED", "COMPLETED") else None
        courier = recovery.courier_plan(case_id)
        if option_id not in (recovery.COURIER, recovery.TECH_TOMORROW) or courier is None:
            return record_only(f"Recorded recovery choice {option_id!r}; no longer available",
                               detail={"optionId": option_id})
        part_id, hub = courier["partId"], courier["courierDepotId"]
        record = {"type": "RECOVERY_CHOSEN", "summary": f"Customer chose {option_id}", "detail": {"optionId": option_id}}
        key = f"{case_id}:recovery:{option_id}"
        if option_id == recovery.TECH_TOMORROW:
            tomorrow = (self._now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            goals: list[Goal] = []
            if courier.get("heldDepotId"):
                goals.append(Goal(
                    "PartsAgent", ["transferPart"], f"Move part {part_id} from the courier hub to the depot.",
                    inputs={"partId": part_id, "fromDepotId": hub, "toDepotId": courier["heldDepotId"],
                            "quantity": 1, "idempotencyKey": f"{key}:transfer"},
                    mode="DIRECT",
                ))
            goals += self._field_planning_goals(case_id, resend_key=f"{key}:slots:{event_id}", start=tomorrow)
            return Plan(
                version=version, goals=goals, target_state=None, record=record,
                set_fields={"recoveryChoice": option_id},
                rationale="Customer chose a technician tomorrow: bring the part back and offer slots from tomorrow.",
            )
        goals = [Goal(
            "PartsAgent", ["findAndReservePart"], f"Confirm part {part_id} at the courier hub {hub}.",
            inputs={"caseId": case_id, "partId": part_id, "mode": "CONFIRM", "depotId": hub,
                    "idempotencyKey": f"{key}:part"},
            mode="DIRECT", stop_on_error=True,
        )]
        if courier.get("heldReservationId"):
            goals.append(Goal(
                "PartsAgent", ["releasePartReservation"], "Release the failed hold at the depot.",
                inputs={"reservationId": courier["heldReservationId"], "idempotencyKey": f"{key}:release"},
                mode="DIRECT",
            ))
        if work_order_id:
            goals.append(Goal(
                "SchedulingDispatchAgent", ["updateWorkOrder"], "Cancel the technician visit.",
                inputs={"workOrderId": work_order_id, "status": "CANCELLED", "idempotencyKey": f"{key}:cancel"},
                mode="DIRECT", stop_on_error=True,
            ))
        goals += [
            Goal(
                "ServiceRecoveryAgent", ["dispatchCourier"],
                "Dispatch the courier; the customer gets the ETA and handover code on WhatsApp and the guide by email.",
                inputs={"caseId": case_id, "partId": part_id, "fromDepotId": hub, "contactId": PRIMARY_CONTACT,
                        "idempotencyKey": f"{key}:dispatch"},
                mode="DIRECT", stop_on_error=True,
            ),
            Goal(
                "CommunicationAgent", ["sendCustomerMessage"], "Ask the customer to tap Installed when done.",
                inputs={
                    "caseId": case_id, "contactId": PRIMARY_CONTACT, "intent": "CONFIRMATION",
                    "data": {
                        "body": "When the new fibre box is installed and its light is solid green, tap Installed.",
                        "options": [{"optionId": "-", "label": "Installed", "intent": recovery.SELF_INSTALL_DONE}],
                    },
                    "idempotencyKey": f"{key}:installed",
                },
                mode="DIRECT",
            ),
        ]
        return Plan(
            version=version, goals=goals, target_state=None, success_state=CaseState.SCHEDULED.value,
            record=record, set_fields={"recoveryChoice": option_id, "resolutionPath": "COURIER_SELF_INSTALL"},
            rationale="Customer chose the courier: confirm the part, cancel the visit, dispatch, ask for Installed.",
        )

    def _approval_decided(self, version, state, case_id, asset_id, payload, record_only, customer_id=None) -> Plan:
        action = str(payload.get("action") or "")
        decision = payload.get("decision")
        approval_id = payload.get("approvalId")
        detail = {"approvalId": approval_id, "decision": decision, "action": action or None}

        # Downtime credit above the auto-apply cap: apply on approval, any state (the case may be closed already).
        if action.startswith(_SERVICE_CREDIT_OVER_CAP):
            amount = action[len(_SERVICE_CREDIT_OVER_CAP):]
            if decision == _APPROVED:
                return Plan(
                    version=version,
                    goals=[Goal(
                        "CompletionAgent", ["applyServiceCredit"], f"Apply the service credit approved in {approval_id}.",
                        inputs={"caseId": case_id, "customerId": customer_id, "amountUsd": float(amount),
                                "reason": "Downtime credit approved by a care team lead", "approvalId": approval_id,
                                "idempotencyKey": f"{case_id}:credit:{approval_id}"},
                        mode="DIRECT",
                    )],
                    target_state=None, rationale=f"Service credit ${amount} approved in {approval_id}: apply it.",
                    record={"type": "SERVICE_CREDIT_APPROVED", "summary": f"Service credit of ${amount} approved; applying it",
                            "detail": {**detail, "amountUsd": amount}},
                )
            return record_only(
                f"Service credit of ${amount} {decision}", event_type="SERVICE_CREDIT_REJECTED",
                detail={**detail, "amountUsd": amount},
            )

        # Technician part request from the mobile app: reserve on approval, any state.
        if action.startswith(_PART_REQUEST):
            part_id = action[len(_PART_REQUEST):]
            summary = f"Part request {approval_id} for {part_id} {decision}"
            record = {"type": "PART_REQUEST_DECIDED", "summary": summary, "detail": {**detail, "partId": part_id}}
            goals = []
            if decision == _APPROVED:
                goals.append(Goal(
                    "PartsAgent", ["findAndReservePart"],
                    f"Reserve part {part_id} approved in {approval_id} for the technician.",
                    inputs={
                        "caseId": case_id, "partId": part_id, "mode": "CONFIRM",
                        "approvalId": approval_id, "idempotencyKey": f"{case_id}:part:{approval_id}",
                    },
                    mode="DIRECT",
                ))
            return Plan(version=version, goals=goals, target_state=None, rationale=summary, record=record)

        if state == CaseState.AWAITING_APPROVAL.value:
            primary = action.startswith(_PART_OVER_THRESHOLD) or action.startswith(_SAFE_REMOTE_ACTION)
            if primary and decision == _REJECTED:
                return Plan(
                    version=version, goals=[], target_state=CaseState.ESCALATED_HUMAN.value,
                    rationale=f"Approval {approval_id} rejected: {action}; human takeover",
                )
            if action.startswith(_PART_OVER_THRESHOLD) and decision == _APPROVED:
                part_id = action[len(_PART_OVER_THRESHOLD):]
                return Plan(
                    version=version,
                    goals=[
                        Goal(
                            "PartsAgent", ["findAndReservePart"],
                            f"Hold part {part_id} approved in {approval_id}.",
                            inputs={
                                "caseId": case_id, "partId": part_id, "mode": "HOLD",
                                "approvalId": approval_id, "idempotencyKey": f"{case_id}:part:{approval_id}",
                            },
                            mode="DIRECT",
                        ),
                        *self._field_planning_goals(case_id),
                    ],
                    target_state=CaseState.PLANNING_FIELD.value,
                    rationale=f"Approval {approval_id} granted for part {part_id}: hold it and plan the visit.",
                )
            if action.startswith(_SAFE_REMOTE_ACTION) and decision == _APPROVED:
                # Consent-bound: run exactly the approved action, no LLM. The Gateway re-checks the approval.
                action_name = action[len(_SAFE_REMOTE_ACTION):]
                return Plan(
                    version=version,
                    goals=[Goal(
                        "DiagnosticAgent", ["executeSafeRemoteAction"],
                        f"Execute the remote action {action_name} approved in {approval_id}.",
                        inputs={
                            "caseId": case_id, "assetId": asset_id, "action": action_name,
                            "approvalId": approval_id, "idempotencyKey": f"{case_id}:remote:{approval_id}",
                        },
                        mode="DIRECT",
                    )],
                    target_state=CaseState.REMOTE_FIX_IN_PROGRESS.value,
                    rationale=f"Approval {approval_id} granted: execute the remote action {action_name}.",
                )

        return record_only(f"Recorded approval {approval_id} {decision} in {state}; no transition", detail=detail)

    def _postback(self, version, state, case_id, event_id, payload, flags, record_only, envelope=None) -> Plan:
        intent = payload.get("intent")
        option_id = payload.get("optionId")
        envelope = envelope or {}

        if intent == recovery.RECOVERY_CHOICE:
            if state == CaseState.AT_RISK.value:
                return self._recovery_choice(version, case_id, event_id, option_id, envelope, record_only)
            return record_only(f"Recorded recovery tap in {state}; no transition",
                               detail={"intent": intent, "optionId": option_id})

        if intent == recovery.SELF_INSTALL_DONE:
            if state in (CaseState.SCHEDULED.value, CaseState.IN_SERVICE.value):
                return Plan(
                    version=version, goals=[self._csat_goal(case_id, event_id)], target_state=CaseState.VERIFYING.value,
                    via_state=CaseState.IN_SERVICE.value if state == CaseState.SCHEDULED.value else None,
                    rationale="Customer finished the self-install: request CSAT, verify the repair.",
                    record={"type": "SELF_INSTALL_DONE", "summary": "Customer confirmed the self-install", "detail": {}},
                )
            return record_only(f"Recorded Installed tap in {state}; no transition")

        if intent == "CLARIFY_REPLY" and state == CaseState.AWAITING_CUSTOMER.value:
            return self._rediagnose(
                version, f"The customer answered by choosing option {option_id!r}.",
                {"type": "CUSTOMER_REPLIED", "summary": "Customer answered the question; re-running the diagnosis",
                 "detail": {"intent": intent, "optionId": option_id}},
                "Customer answered: re-run the diagnosis.",
            )

        if intent == "BOOK_SLOT":
            parsed = parse_slot_option(option_id)
            if parsed and state in (CaseState.PLANNING_FIELD.value, CaseState.AT_RISK.value):
                technician_id, slot_start, slot_end = parsed
                goals = []
                appt = envelope.get("appointment") or {}
                if state == CaseState.AT_RISK.value and appt.get("workOrderId") \
                        and appt.get("status") not in ("CANCELLED", "COMPLETED"):
                    # Rebooking after a recovery: the original visit goes first (frees its windows and its job).
                    goals.append(Goal(
                        "SchedulingDispatchAgent", ["updateWorkOrder"], "Cancel the original visit before rebooking.",
                        inputs={"workOrderId": appt["workOrderId"], "status": "CANCELLED",
                                "idempotencyKey": f"{case_id}:rebook-cancel:{event_id}"},
                        mode="DIRECT", stop_on_error=True,
                    ))
                goals.append(Goal(
                    "SchedulingDispatchAgent", ["createServiceAppointment"],
                    "Customer picked a slot: book it.",
                    inputs={
                        "caseId": case_id, "technicianId": technician_id,
                        "slotStart": slot_start, "slotEnd": slot_end,
                        "consentRef": event_id, "idempotencyKey": f"{case_id}:book:{event_id}",
                    },
                    mode="DIRECT",
                ))
                return Plan(
                    version=version,
                    goals=goals,
                    target_state=None,
                    success_state=CaseState.SCHEDULED.value,
                    rationale="Verified slot tap: book the appointment.",
                )
            reason = "unparseable slot option" if not parsed else f"case is {state}"
            return record_only(f"Recorded BOOK_SLOT tap ({reason}); not booked",
                               detail={"intent": intent, "optionId": option_id})

        if intent == "CSAT":
            return self._csat(version, state, _csat_score(option_id), flags, record_only)

        if intent == "SHOW_UPDATE" and state == CaseState.PLANNING_FIELD.value:
            # "View update" template tap (sent while the 24h window was closed). The tap opened the window, so
            # re-offer the slots now as the free-form list.
            return Plan(
                version=version,
                goals=self._field_planning_goals(case_id, resend_key=f"{case_id}:msg:APPOINTMENT_OPTIONS:{event_id}"),
                target_state=None,
                rationale="Customer tapped View update: re-offer the appointment slots (window is open now).",
            )

        if intent == "HUMAN":
            return Plan(
                version=version, goals=[], target_state=CaseState.ESCALATED_HUMAN.value,
                rationale="Customer asked to talk to a person; human takeover.",
            )

        return record_only(f"Recorded {intent} tap in {state}; no transition",
                           detail={"intent": intent, "optionId": option_id})

    def _customer_reply(self, version, state, case_id, payload, flags, record_only) -> Plan:
        kind = payload.get("kind")
        if payload.get("guardrailBlocked"):  # prompt attack etc.: no specialist, no tool call
            return record_only(f"Blocked a customer message in {state} (guardrail); no action taken",
                               event_type="SECURITY_EVENT", detail={"state": state})
        if state == CaseState.VERIFYING.value:
            score = _csat_score(payload.get("text"))
            if score is not None:
                return self._csat(version, state, score, flags, record_only)
        if kind == "OPT_OUT":
            return record_only(f"Recorded customer opt-out in {state}", detail={"kind": kind})
        if state == CaseState.AWAITING_CUSTOMER.value:
            return self._rediagnose(
                version, "The customer's answer is inside <customer_message>.",
                {"type": "CUSTOMER_REPLIED", "summary": "Customer answered the question; re-running the diagnosis",
                 "detail": {"kind": kind}},
                "Customer answered: re-run the diagnosis.",
            )
        return Plan(
            version=version,
            goals=[Goal(
                "CommunicationAgent", ["sendCustomerMessage"],
                "Acknowledge the customer message inside <customer_message> and say we will follow up; "
                "do not promise times.",
                inputs={"caseId": case_id, "contactId": PRIMARY_CONTACT, "intent": "CONFIRMATION"},
            )],
            target_state=None,
            rationale=f"Customer message in {state}: acknowledge, stay in state.",
        )

    @staticmethod
    def _csat(version, state, score, flags, record_only) -> Plan:
        """A CSAT score. In VERIFYING it is stored and the case closes once the verification window has
        also elapsed (either order); elsewhere it is only recorded."""
        if score is None:
            return record_only(f"Recorded CSAT tap with an invalid score in {state}")
        if state != CaseState.VERIFYING.value:
            return record_only(f"Customer CSAT {score}/5", event_type="CSAT_RECEIVED", detail={"score": score})
        if flags.get("csatScore") is not None:  # one score per case: a second tap would skew the CSAT average
            return record_only(f"Ignored a second CSAT ({score}/5); {flags['csatScore']}/5 is already on record")
        window_over = flags.get("verifyWindowElapsedAt") is not None
        plan = record_only(
            f"Customer CSAT {score}/5; "
            + ("verification window already elapsed, closing the case" if window_over
               else "waiting for the verification window to end"),
            event_type="CSAT_RECEIVED",
            detail={"score": score},
            target=CaseState.CLOSED.value if window_over else None,
        )
        plan.set_fields = {"csatScore": score}
        return plan
