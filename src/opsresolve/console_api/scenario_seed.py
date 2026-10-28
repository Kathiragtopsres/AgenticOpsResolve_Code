"""Baseline demo data for the Ops Console, per tenant: S1 (meravix-foods) 6 cases + 5 approvals, S2
(meravix-broadband) 4 cases + 1 approval (see ``build_baseline(now, tenant_id)``).

``build_baseline(now)`` returns the exact item lists that ``POST /scenario/reset`` (and
``scripts/seed_console_demo.py``) write. Everything is single-table, case_store.py layout:

  cases table   pk=CASE#<id>  sk=META | EVT#<ts>#<evtId> | PLAN#v<n> | APPT#<woId> | MSG#<ts>#<msgId>
                META carries gsi1pk/gsi1sk (STATE#<state> / <priority>#<restoreBy|createdAt>)
                and gsi2pk/gsi2sk (ASSET#<assetId> / createdAt)
  approvals     hash key approvalId only (Lakshmi's requestApproval attributes)

Demo rule (decided 2026-10-15): the live demo opens ONE new case on CHILLER-8821 (SiteWise anomaly via
``POST /scenario/telemetry``) and walks it to closure, creating its approvals and mobile job on the way.
So the baseline has:
  - 5 active cases, exactly one per asset other than CHILLER-8821, none in OPEN/CONTEXT_READY (the
    Open column stays empty for the demo case): Diagnosing x2, Scheduled, At Risk, In Service;
  - 2 CLOSED cases (one on CHILLER-8821, one on CHILLER-8822) and 1 CANCELLED (AHU-9011). Terminal
    cases never block the orchestrator's one-open-case-per-asset merge, so the demo case is always new.
TECH-204 (R. Kumar, the mobile-app technician) has one filler job: ``SR-FIELD-TECH204-01`` on CHILLER-8822,
SCHEDULED, with checklist/manualRef/fieldBrief on the APPT# item. No seeded APPT# exists on CHILLER-8821.

All timestamps are relative to ``now`` so the KPI window always shows data. KPI panel after a Reset
(``GET /kpis``, default 8 weeks): MTTR 175.0 min over 2 closed cases (130 + 220), SLA breaches avoided 2,
remote resolution 0.5 (1 of 2 closed cases had no field visit), CSAT 4.5 from 2 responses (4 + 5),
cases by channel WHATSAPP 3 / SMS 2 / EMAIL 2 / RCS 1. Messages/CSAT/SLA events use the contract pinned
in ``_common.py``. ``INVENTORY_ON_HAND`` and ``ORIGINAL_WORK_ORDER_IDS`` are the part-stock baseline and
the original mock work orders that Reset keeps.

Tenants: the two broadband cases (SR-A2F15D8B60, SR-91B6E3D07A) and the S2-only cases belong to
``meravix-broadband`` (ids in ``S2_CASE_IDS``); everything else is ``meravix-foods``. Every seeded item
carries ``tenantId``.

Every META carries the Case Information fields from ``CASE_DETAILS``: category, subcategory,
assignedQueue, caseOwner, customerDescription, sourceChannel, and requiredPart where a part is needed.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from ._common import EVT_CSAT, EVT_SLA_BREACH_AVOIDED

PROMPT_VERSION = "orchestrator.v1.md"
_ORCH = {"type": "AGENT", "name": "ServiceResolutionOrchestrator", "promptVersion": PROMPT_VERSION}

# --- Case Information fields (added to every META in build_baseline) ----------------------
Q_COLD = "Cold-Chain Field Service · South"
Q_HVAC = "HVAC Field Service · South"
Q_IMG = "Imaging Uptime · Clinical Engineering"
Q_BB = "Broadband Home Support · South"
ORCH_OWNER = "OpsResolve Orchestrator"

# Parts from the kit parts catalog: {partId, name, qty, unitValueUsd}.
_PARTS: dict[str, dict[str, Any]] = {
    "CTRL-BRD-900": {"partId": "CTRL-BRD-900", "name": "CX-500 control board, extended (rev B)", "qty": 1,
                     "unitValueUsd": 1240},
    "ONT-X200": {"partId": "ONT-X200", "name": "Fibre ONT X200", "qty": 1, "unitValueUsd": 85},
    "CRYO-ADS-3T": {"partId": "CRYO-ADS-3T", "name": "Cryo compressor adsorber module", "qty": 1,
                    "unitValueUsd": 2150},
    "FLT-AHU-90": {"partId": "FLT-AHU-90", "name": "AHU-90 pleated filter set (F7)", "qty": 4, "unitValueUsd": 38},
    "CTRL-BRD-500": {"partId": "CTRL-BRD-500", "name": "CX-500 control board (rev A)", "qty": 1,
                     "unitValueUsd": 420},
}

# C7: part stock that Reset restores in opsresolve-dev-mock-inventory (pk=INV#<depot>#<part>, sk=META).
# A fixed, generous amount (not the live onHand, which demos drain via PART_USED). Reset only SETs
# onHand on these rows (attribute_exists(pk)); it never creates rows or touches other attributes.
INVENTORY_ON_HAND: dict[str, int] = {
    "INV#DEP-BLR-01#CTRL-BRD-500": 50,
    "INV#DEP-CHN-01#CTRL-BRD-500": 50,
    "INV#DEP-CHN-01#CTRL-BRD-900": 50,
    "INV#DEP-CHN-01#PRS-SNS-55": 50,
    "INV#DEP-CHN-M1#CRYO-ADS-3T": 50,
}
# S2: the Chennai depot is down to the one ONT the failed pick promised; the courier hub has stock.
INVENTORY_ON_HAND_S2: dict[str, int] = {
    "INV#DEP-CHN-02#ONT-X200": 1,
    "INV#HUB-CHN-C1#ONT-X200": 5,
}

DEFAULT_TENANT = "meravix-foods"
S2_TENANT = "meravix-broadband"
INVENTORY_BY_TENANT: dict[str, dict[str, int]] = {DEFAULT_TENANT: INVENTORY_ON_HAND, S2_TENANT: INVENTORY_ON_HAND_S2}

# C8: the original mock work orders (ids from seed/s1-cold-chain-chiller/work_order_history.json,
# hardcoded because the Lambda zip does not ship seed/). Reset deletes every other row of
# opsresolve-dev-mock-work-order-history (rows created by demos, e.g. Gateway reassignTechnician).
ORIGINAL_WORK_ORDER_IDS: frozenset[str] = frozenset({
    "WO-76000", "WO-76037", "WO-76074", "WO-76111", "WO-76148", "WO-76185", "WO-76222", "WO-76259",
    "WO-76296", "WO-76333", "WO-76370", "WO-76407", "WO-76444", "WO-76481", "WO-76518", "WO-76555",
    "WO-76592", "WO-76629", "WO-76666", "WO-76703",
})
# S2 original mock work orders (seed/s2-broadband-whatsapp/work_order_history.json); Reset keeps them too.
ORIGINAL_WORK_ORDER_IDS_S2: frozenset[str] = frozenset({"WO-82001", "WO-82002", "WO-82003", "WO-82004", "WO-82005"})
ORIGINAL_WORK_ORDER_IDS_BY_TENANT: dict[str, frozenset[str]] = {
    DEFAULT_TENANT: ORIGINAL_WORK_ORDER_IDS, S2_TENANT: ORIGINAL_WORK_ORDER_IDS_S2,
}

# The customer's first message on SR-A2F15D8B60 (the one customer-opened case).
_A2F1_FIRST_MSG = "Hi, my internet has been down since this morning. The router lights are all red."

_SUB_SUPPLY = "Cold-chain chiller · supply temperature"


def _details(category: str, subcategory: str, queue: str, owner: str, source: str, description: str,
             part: str | None = None, title: str | None = None) -> dict[str, Any]:
    return {"title": title, "category": category, "subcategory": subcategory, "assignedQueue": queue,
            "caseOwner": owner, "sourceChannel": source, "customerDescription": description,
            "requiredPart": dict(_PARTS[part]) if part else None}


# Case ids of the 2 cases added on 2026-10-15 (closed remote fix on CHILLER-8821, cancelled AHU callout).
CLOSED_8821_ID = "SR-4C21E9B807"
CANCELLED_AHU_ID = "SR-7E19D04B2A"

CLOSED_COURIER_ID = "SR-3F8A62D1C4"  # S2: closed courier self-install (credit applied, CSAT 5)
CLOSED_VISIT_ID = "SR-6D2B90E7A1"  # S2: closed technician visit (credit above the cap, approval pending)
S2_CASE_IDS = frozenset({"SR-A2F15D8B60", "SR-91B6E3D07A", CLOSED_COURIER_ID, CLOSED_VISIT_ID})

CASE_DETAILS: dict[str, dict[str, Any]] = {
    # --- 5 active cases, one per asset except CHILLER-8821 ---
    "SR-5E20B84F19": _details(
        "Medical Imaging", "MRI · gradient amplifier", Q_IMG, ORCH_OWNER, "IOT_TELEMETRY",
        "MRI-4410 (MRX-3T) raised GRAD_AMP_OVERTEMP: gradient amplifier over temperature, scanning paused. "
        "Regulated asset, so a remote restart needs OEM approval."),
    "SR-A2F15D8B60": _details("Broadband", "Home gateway · offline", Q_BB, ORCH_OWNER, "WHATSAPP", _A2F1_FIRST_MSG),
    "SR-FIELD-TECH204-01": _details(
        "Refrigeration", "Cold-chain chiller · compressor control", Q_COLD, ORCH_OWNER, "IOT_TELEMETRY",
        "CHILLER-8822 compressor cycling irregularly with pressure variance near the 0.18 alarm; a remote soft "
        "reset gave no lasting change, so a technician visit is booked."),
    "SR-91B6E3D07A": _details(
        "Broadband", "Fibre ONT · link stability", Q_BB, "Rahul Mehta (Service Manager)", "IOT_TELEMETRY",
        "Network monitoring detected repeated optical link drops on ONT-55012 (loss of signal several times an "
        "hour); a remote ONT reboot did not stabilise the link.", part="ONT-X200"),
    "SR-C7D4418A6E": _details(
        "HVAC", "Air handling unit · filter", Q_HVAC, ORCH_OWNER, "IOT_TELEMETRY",
        "AHU-9011 filter differential pressure is above its limit: filters clogged and airflow reduced in Plant A.",
        part="FLT-AHU-90"),
    # --- 2 closed, 1 cancelled ---
    CLOSED_8821_ID: _details(
        "Refrigeration", _SUB_SUPPLY, Q_COLD, ORCH_OWNER, "IOT_TELEMETRY",
        "CHILLER-8821 supply temperature drifted above the 6 °C setpoint with compressor pressure variance rising; "
        "a safe remote controller reset brought it back in band."),
    "SR-0B7D9C5E34": _details(
        "Refrigeration", _SUB_SUPPLY, Q_COLD, ORCH_OWNER, "IOT_TELEMETRY",
        "CHILLER-8822 supply temperature is above setpoint and a controller soft reset made no change.",
        part="CTRL-BRD-900"),
    CLOSED_COURIER_ID: _details(
        "Broadband", "Fibre ONT - link stability", Q_BB, ORCH_OWNER, "WHATSAPP",
        "My internet keeps dropping every few minutes.", part="ONT-X200"),
    CLOSED_VISIT_ID: _details(
        "Broadband", "Home gateway - offline", Q_BB, ORCH_OWNER, "WHATSAPP",
        "The router lights are all red and nothing connects."),
    CANCELLED_AHU_ID: _details(
        "HVAC", "Air handling unit · supply fan", Q_HVAC, ORCH_OWNER, "IOT_TELEMETRY",
        "AHU-9011 supply fan vibration went above its alarm limit overnight; the fan was already due for "
        "inspection in the next planned maintenance visit."),
}

# C3: checklist, manual reference and field brief on TECH-204's seeded mobile job.
_TECH204_CHECKLIST = (
    "Isolate power and apply lock-out",
    "Replace or reseat the compressor control board",
    "Run the compressor 20 min and confirm supply temp <= 6 C",
)
_TECH204_MANUAL_REF = "KB-CX500-017"
# S2: checklist on the seeded ONT swap job (TEL-804); without it the mobile "complete" call answers "checklist is required".
_ONT_SWAP_CHECKLIST = (
    "Isolate power to the ONT and lock out",
    "Inspect fibre connector for dirt or damage",
    "Replace the ONT with a new ONT-X200",
    "Reconnect power and LAN cables",
    "Power on and wait for PON light to turn solid green (up to 5 minutes)",
    "Verify internet connectivity on downstream devices",
)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _hid(*parts: str) -> str:
    return hashlib.md5("|".join(parts).encode()).hexdigest()[:12]


def _msg_item(case_id: str, ts: str, channel: str, direction: str, body: str) -> dict[str, Any]:
    mid = f"msg-{_hid(case_id, ts, body)}"
    return {"pk": f"CASE#{case_id}", "sk": f"MSG#{ts}#{mid}", "messageId": mid,
            "channel": channel, "direction": direction, "body": body, "sentAt": ts}


class _Case:
    """Accumulates the items of one case."""

    def __init__(self, case_id: str, *, customer_id: str, asset_id: str, state: str, created_at: str,
                 priority: str | None, restore_by: str | None, correlation_id: str,
                 closed_at: str | None = None, version: int = 2) -> None:
        self.id = case_id
        self.corr = correlation_id
        self.items: list[dict[str, Any]] = []
        self.n = 0
        self.items.append({
            "pk": f"CASE#{case_id}", "sk": "META", "caseId": case_id, "state": state,
            "version": version, "priority": priority, "customerId": customer_id,
            "assetId": asset_id, "restoreBy": restore_by, "correlationId": correlation_id,
            "createdAt": created_at, "closedAt": closed_at,
            "gsi1pk": f"STATE#{state}", "gsi1sk": f"{priority or 'P4'}#{restore_by or created_at}",
            "gsi2pk": f"ASSET#{asset_id}", "gsi2sk": created_at,
        })

    def evt(self, ts: str, etype: str, actor: dict, summary: str, detail: dict | None = None, *,
            tool_name: str | None = None, policy: str | None = None, evt_id: str | None = None) -> None:
        self.n += 1
        eid = evt_id or f"evt-{_hid(self.id, str(self.n), etype)}"
        self.items.append({
            "pk": f"CASE#{self.id}", "sk": f"EVT#{ts}#{eid}", "type": etype, "actor": actor,
            "summary": summary, "toolName": tool_name, "policyDecision": policy,
            "correlationId": self.corr, "detail": detail or {},
        })

    def state(self, ts: str, frm: str, to: str, reason: str) -> None:
        self.evt(ts, "CASE_STATE_CHANGED", _ORCH, f"{frm} -> {to} ({reason})",
                 {"from": frm, "to": to, "reason": reason})

    def tool(self, ts: str, specialist: str, tool: str, tin: dict, tout: dict, latency_ms: int, *,
             status: str = "OK", policy: str = "ALLOW") -> None:
        """Tool-call event carrying the T3 trace fields (latencyMs/status/masked input+output)."""
        self.evt(ts, "TOOL_CALLED", {"type": "AGENT", "name": specialist},
                 f"{specialist} called {tool} ({status})",
                 {"specialist": specialist, "latencyMs": latency_ms, "status": status,
                  "input": tin, "output": tout}, tool_name=tool, policy=policy)

    def msg(self, ts: str, channel: str, direction: str, body: str) -> None:
        self.items.append(_msg_item(self.id, ts, channel, direction, body))

    def plan(self, version: int, goals: list[dict], rationale: str) -> None:
        self.items.append({"pk": f"CASE#{self.id}", "sk": f"PLAN#v{version}", "goals": goals,
                           "rationale": rationale, "promptVersion": PROMPT_VERSION})

    def appt(self, wo: str, tech: str, start: str, end: str, status: str, **extra: Any) -> None:
        """APPT# item; ``extra`` carries mobile-job attributes (checklist, manualRef, fieldBrief)."""
        self.items.append({"pk": f"CASE#{self.id}", "sk": f"APPT#{wo}", "technicianId": tech,
                           "slotStart": start, "slotEnd": end, "status": status, **extra})


# --- The 8 baseline cases (relative to now) ---------------------------------------------------

def _cases(now: datetime) -> tuple[list[dict], list[dict]]:
    items: list[dict] = []
    approvals: list[dict] = []

    def at(base: datetime, **kw: float) -> str:
        return _iso(base + timedelta(**kw))

    def opened(c: _Case, ts: str, title: str, *, trigger_type: str = "TELEMETRY", channel: str | None = None) -> None:
        trigger = {"eventId": f"evt-{_hid(c.id, 'trg')}", "type": trigger_type, "name": title}
        if channel:
            trigger["channel"] = channel
        c.evt(ts, "CASE_OPENED", {"type": "SYSTEM", "name": "CaseOpener"}, "Case opened", {"trigger": trigger})

    def context(c: _Case, t0: datetime, customer: str, asset: str) -> None:
        c.state(at(t0, minutes=1), "OPEN", "CONTEXT_READY", "New case: assemble context")
        c.tool(at(t0, minutes=1, seconds=10), "AssetContextAgent", "getAssetContext",
               {"assetId": asset}, {"customerId": customer, "assetId": asset, "found": True}, 212)
        c.tool(at(t0, minutes=1, seconds=14), "AssetContextAgent", "getServiceHistory",
               {"assetId": asset}, {"workOrders": 2}, 148)
        c.state(at(t0, minutes=2), "CONTEXT_READY", "DIAGNOSING", "Context assembled")

    def approval(n: int, c: _Case, action: str, amount: str, role: str, approver: str, created: str,
                 status: str = "PENDING", reason: str | None = None, decided_by: str | None = None,
                 decided_at: str | None = None) -> None:
        a = {"approvalId": f"APR-{n:04d}-1", "caseId": c.id, "action": action, "amount": amount,
             "approverRole": role, "notifiedApprover": approver, "status": status,
             "idempotencyKey": f"{c.id}:{action}:{n}", "createdAt": created}
        if status != "PENDING":
            a.update({"reason": reason, "decidedBy": decided_by, "decidedAt": decided_at})
        approvals.append(a)

    # ===== Active: one case per asset except CHILLER-8821, none in OPEN / CONTEXT_READY =====

    # 1) Diagnosing / AWAITING_APPROVAL - MRI-4410, regulated restart needs OEM approval -------
    t0 = now - timedelta(hours=5)
    c = _Case("SR-5E20B84F19", customer_id="CUST-3107", asset_id="MRI-4410", state="AWAITING_APPROVAL",
              created_at=_iso(t0), priority="P1", restore_by=at(t0, hours=8), correlation_id="corr-seed-5e20b84f",
              version=5)
    opened(c, _iso(t0), "MRI gradient amplifier fault")
    context(c, t0, "CUST-3107", "MRI-4410")
    c.tool(at(t0, minutes=4), "DiagnosticAgent", "getAssetHealth", {"assetId": "MRI-4410"},
           {"health": "DEGRADED", "alarm": "GRAD_AMP_OVERTEMP"}, 390)
    c.tool(at(t0, minutes=6), "DiagnosticAgent", "executeSafeRemoteAction",
           {"assetId": "MRI-4410", "action": "gradient_amp_restart"},
           {"error": "NEEDS_APPROVAL"}, 95, status="ERROR", policy="REQUIRE_APPROVAL")
    c.evt(at(t0, minutes=7), "APPROVAL_REQUESTED", {"type": "AGENT", "name": "SLAEntitlementAgent"},
          "Approval requested: remote restart of regulated imaging asset", {"approvalId": "APR-0601-1"},
          tool_name="requestApproval", policy="REQUIRE_APPROVAL")
    c.state(at(t0, minutes=8), "DIAGNOSING", "AWAITING_APPROVAL", "Regulated asset action needs approval")
    c.plan(1, [{"goal": "Restore MRI-4410 imaging", "expectedTools": ["executeSafeRemoteAction", "requestApproval"]}],
           "Remote restart first; dispatch MED technician if it fails.")
    c.msg(at(t0, minutes=3), "RCS", "OUTBOUND", "MRI-4410 fault detected. Our team is diagnosing remotely.")
    c.msg(at(t0, minutes=9), "SMS", "OUTBOUND", "OpsResolve: awaiting OEM approval for a remote restart of MRI-4410.")
    items += c.items
    approval(601, c, "EXECUTE_SAFE_REMOTE_ACTION:gradient_amp_restart", "0", "OEM_REMOTE_SUPPORT", "OEM-RS-01", at(t0, minutes=7))
    approval(602, c, "COURIER_EXPEDITE", "180", "SERVICE_MANAGER", "APR-11", at(t0, minutes=12))

    # 2) Diagnosing / REMOTE_FIX_IN_PROGRESS - RTR-55013, customer-opened on WhatsApp ----------
    t0 = now - timedelta(minutes=35)
    c = _Case("SR-A2F15D8B60", customer_id="CUST-2202", asset_id="RTR-55022", state="REMOTE_FIX_IN_PROGRESS",
              created_at=_iso(t0), priority="P3", restore_by=at(t0, hours=24), correlation_id="corr-seed-a2f15d8b",
              version=4)
    opened(c, _iso(t0), "Home gateway offline", trigger_type="CUSTOMER_REPLY", channel="WHATSAPP")
    c.msg(_iso(t0), "WHATSAPP", "INBOUND", _A2F1_FIRST_MSG)
    context(c, t0, "CUST-2202", "RTR-55022")
    c.tool(at(t0, minutes=4), "DiagnosticAgent", "getAssetHealth", {"assetId": "RTR-55022"},
           {"health": "OFFLINE", "lastSeenMin": 190}, 260)
    c.tool(at(t0, minutes=6), "DiagnosticAgent", "executeSafeRemoteAction",
           {"assetId": "RTR-55022", "action": "reprovision"}, {"status": "IN_PROGRESS", "etaMin": 20}, 3200)
    c.state(at(t0, minutes=6, seconds=30), "DIAGNOSING", "REMOTE_FIX_IN_PROGRESS", "Safe remote reprovision started")
    c.plan(1, [{"goal": "Reprovision gateway remotely", "expectedTools": ["executeSafeRemoteAction"]}],
           "Gateway offline with no hardware fault signature; safe remote reprovision first.")
    c.msg(at(t0, minutes=3), "WHATSAPP", "OUTBOUND", "Hi Karthik, we see your gateway is offline. Checking now.")
    c.msg(at(t0, minutes=7), "WHATSAPP", "OUTBOUND",
          "We are reprovisioning your gateway remotely. It takes about 20 minutes, please keep it powered on.")
    items += c.items

    # 3) Scheduled - CHILLER-8822, TECH-204's filler mobile job (not used in the demo) ----------
    def mobile_job(c: _Case, wo: str, start: datetime, status: str, brief: str) -> None:
        c.appt(wo, "TECH-204", _iso(start), _iso(start + timedelta(hours=2)), status,
               checklist=[{"item": i, "done": False} for i in _TECH204_CHECKLIST],
               manualRef=_TECH204_MANUAL_REF, fieldBrief={"text": brief, "confidence": Decimal("0.8")})

    t0 = now - timedelta(hours=6)
    # 5 days out in the 14-16 UTC window, so the demo case's earliest TECH-204 slots stay free.
    visit = (now + timedelta(days=5)).replace(hour=14, minute=0, second=0)
    c = _Case("SR-FIELD-TECH204-01", customer_id="CUST-1042", asset_id="CHILLER-8822", state="SCHEDULED",
              created_at=_iso(t0), priority="P3", restore_by=_iso(visit + timedelta(hours=4)),
              correlation_id="corr-seed-tech204-01", version=6)
    opened(c, _iso(t0), "Chiller compressor control fault")
    context(c, t0, "CUST-1042", "CHILLER-8822")
    c.tool(at(t0, minutes=5), "DiagnosticAgent", "executeSafeRemoteAction",
           {"assetId": "CHILLER-8822", "action": "controller_soft_reset"}, {"result": "NO_CHANGE"}, 1700)
    c.state(at(t0, minutes=8), "DIAGNOSING", "PLANNING_FIELD", "Remote fix not applicable")
    c.tool(at(t0, minutes=10), "TechnicianMatchingAgent", "findQualifiedTechnicians",
           {"assetId": "CHILLER-8822", "siteId": "PLANT-A"}, {"candidates": ["TECH-204", "TECH-118"]}, 260)
    c.state(at(t0, minutes=15), "PLANNING_FIELD", "SCHEDULED", "Customer confirmed slot")
    c.plan(1, [{"goal": "Repair compressor control", "expectedTools": ["findQualifiedTechnicians",
                                                                     "createServiceAppointment"]}],
           "Remote reset ineffective; dispatch the CX-500 technician TECH-204.")
    mobile_job(c, "WO-88201", visit, "SCHEDULED",
               "Compressor pressure variance near alarm after a failed soft reset; inspect and reseat the control "
               "board, replace if the fault persists.")
    c.msg(at(t0, minutes=16), "WHATSAPP", "OUTBOUND",
          f"Your CHILLER-8822 visit is booked for {visit.strftime('%a %d %b')} 14:00-16:00 UTC.")
    items += c.items

    # 4) At Risk - ONT-55031 home broadband, technician running late ---------------------------
    t0 = now - timedelta(hours=26)
    c = _Case("SR-91B6E3D07A", customer_id="CUST-2203", asset_id="ONT-55031", state="AT_RISK",
              created_at=_iso(t0), priority="P2", restore_by=at(now, minutes=40), correlation_id="corr-seed-91b6e3d0",
              version=8)
    opened(c, _iso(t0), "Fibre ONT link flapping")
    context(c, t0, "CUST-2203", "ONT-55031")
    c.tool(at(t0, minutes=5), "DiagnosticAgent", "executeSafeRemoteAction",
           {"assetId": "ONT-55031", "action": "reboot_ont"}, {"result": "NO_CHANGE"}, 2100)
    c.state(at(t0, minutes=8), "DIAGNOSING", "PLANNING_FIELD", "Remote reboot ineffective")
    c.state(at(t0, hours=1), "PLANNING_FIELD", "SCHEDULED", "Visit booked")
    c.evt(at(now, hours=-1), "TECHNICIAN_ETA_UPDATED", {"type": "SYSTEM", "name": "mock-wfm"},
          "Technician ETA pushed out by 100 minutes", {"technicianId": "TEL-804", "currentJobRemainingMin": 100, "travelMin": 60})
    c.state(at(now, minutes=-55), "SCHEDULED", "AT_RISK", "ETA breaches SLA window")
    c.plan(1, [{"goal": "Replace ONT", "expectedTools": ["createServiceAppointment"]}], "Field swap of customer-replaceable ONT.")
    c.plan(2, [{"goal": "Recover SLA", "expectedTools": ["reassignTechnician", "sendCustomerMessage"]}],
           "ETA slip; reassign or offer courier swap.")
    c.appt("WO-88102", "TEL-804", at(now, days=5), at(now, days=5, hours=2), "DELAYED",
           checklist=[{"item": i, "done": False} for i in _ONT_SWAP_CHECKLIST],
           manualRef={"title": "ONT-X200 replacement guide",
                      "excerpt": "Section 3.1: ONT swap, PON light verification and downstream connectivity check."})
    c.msg(at(t0, minutes=7), "WHATSAPP", "OUTBOUND", "Hi, we are looking into your fibre connection.")
    c.msg(at(now, minutes=-50), "WHATSAPP", "OUTBOUND", "Your technician is running late. Would you like a courier swap instead?")
    c.msg(at(now, minutes=-45), "WHATSAPP", "INBOUND", "Courier swap works, thanks.")
    items += c.items

    # 5) In Service - AHU-9011 filter swap, technician on site ----------------------------------
    t0 = now - timedelta(hours=20)
    on_site = (now - timedelta(minutes=40)).replace(second=0)
    c = _Case("SR-C7D4418A6E", customer_id="CUST-1042", asset_id="AHU-9011", state="IN_SERVICE",
              created_at=_iso(t0), priority="P3", restore_by=at(now, hours=2), correlation_id="corr-seed-c7d4418a",
              version=7)
    opened(c, _iso(t0), "AHU filter differential pressure high")
    context(c, t0, "CUST-1042", "AHU-9011")
    c.tool(at(t0, minutes=6), "TechnicianMatchingAgent", "findQualifiedTechnicians",
           {"assetId": "AHU-9011", "siteId": "PLANT-A"}, {"candidates": ["TECH-615", "TECH-402"]}, 270)
    c.state(at(t0, minutes=8), "DIAGNOSING", "PLANNING_FIELD", "Remote fix not applicable")
    c.tool(at(t0, minutes=9), "SchedulingAgent", "createServiceAppointment",
           {"technicianId": "TECH-615", "slot": _iso(on_site)}, {"workOrderId": "WO-88101", "status": "CONFIRMED"}, 330)
    c.state(at(t0, minutes=14), "PLANNING_FIELD", "SCHEDULED", "Customer confirmed slot")
    c.state(_iso(on_site), "SCHEDULED", "IN_SERVICE", "Technician arrived on site")
    c.plan(1, [{"goal": "Replace AHU filter", "expectedTools": ["findQualifiedTechnicians", "createServiceAppointment"]}],
           "Field visit required; book next qualified technician before production starts.")
    c.appt("WO-88101", "TECH-615", _iso(on_site), _iso(on_site + timedelta(hours=2)), "IN_PROGRESS")
    c.msg(at(t0, minutes=12), "EMAIL", "OUTBOUND", "Subject: Proposed visit slots for AHU-9011.")
    c.msg(at(t0, minutes=30), "EMAIL", "INBOUND", "Slot confirmed, see you then.")
    c.msg(at(t0, minutes=31), "EMAIL", "OUTBOUND", "Subject: Booking confirmation WO-88101.")
    items += c.items
    approval(604, c, "AFTER_HOURS_VISIT", "0", "SERVICE_MANAGER", "APR-11", at(t0, minutes=13),
             status="APPROVED", reason="Early visit before production starts, as the site asked.",
             decided_by="demo.approver", decided_at=at(t0, minutes=21))

    # ===== Terminal: 2 closed (one on CHILLER-8821), 1 cancelled ==================================

    # 6) Closed ~12 days ago - CHILLER-8821 fixed REMOTELY (no APPT#), CSAT 4, SLA breach avoided ---
    t0 = now - timedelta(days=12, hours=2)
    closed = t0 + timedelta(hours=2, minutes=10)
    c = _Case(CLOSED_8821_ID, customer_id="CUST-1042", asset_id="CHILLER-8821", state="CLOSED",
              created_at=_iso(t0), priority="P1", restore_by=_iso(t0 + timedelta(hours=4)),
              correlation_id="corr-seed-4c21e9b8", closed_at=_iso(closed), version=7)
    opened(c, _iso(t0), "Chiller supply temperature trending high")
    context(c, t0, "CUST-1042", "CHILLER-8821")
    c.tool(at(t0, minutes=3), "DiagnosticAgent", "getAssetHealth", {"assetId": "CHILLER-8821"},
           {"health": "DEGRADED", "supplyTempC": Decimal("7.4"), "compressorPressureVar": Decimal("0.16")}, 380)
    c.tool(at(t0, minutes=5), "KnowledgeAgent", "retrieveTroubleshootingKnowledge",
           {"query": "CX-500 supply temperature above setpoint, pressure variance rising"},
           {"hits": 2, "topDoc": "KB-CX500-017"}, 610)
    c.tool(at(t0, minutes=8), "DiagnosticAgent", "executeSafeRemoteAction",
           {"assetId": "CHILLER-8821", "action": "controller_soft_reset"}, {"result": "RECOVERED"}, 1900)
    c.state(at(t0, minutes=8, seconds=30), "DIAGNOSING", "REMOTE_FIX_IN_PROGRESS", "Safe remote controller reset started")
    c.state(at(t0, minutes=40), "REMOTE_FIX_IN_PROGRESS", "RESOLVED_REMOTE", "Supply temperature back below 6 °C")
    c.evt(at(t0, minutes=40, seconds=5), EVT_SLA_BREACH_AVOIDED, {"type": "SYSTEM", "name": "ServiceRecoveryAgent"},
          "SLA breach avoided: fixed remotely inside the 4-hour restore window",
          {"restoreBy": _iso(t0 + timedelta(hours=4)), "recoveredAt": at(t0, minutes=40)})
    c.state(at(t0, minutes=45), "RESOLVED_REMOTE", "VERIFYING", "Monitoring readings after the reset")
    c.evt(at(t0, hours=2, minutes=9), EVT_CSAT, {"type": "CUSTOMER", "name": "Anita Rao"}, "CSAT received: 4",
          {"score": 4})
    c.state(_iso(closed), "VERIFYING", "CLOSED", "Verified healthy, customer feedback received")
    c.plan(1, [{"goal": "Restore CHILLER-8821 supply temperature",
                "expectedTools": ["retrieveTroubleshootingKnowledge", "executeSafeRemoteAction"]}],
           "Pressure variance below the board-fault level; safe controller reset first.")
    c.msg(at(t0, minutes=2), "RCS", "OUTBOUND",
          "Predicted chiller failure on CHILLER-8821: supply temperature trending high. We are fixing it remotely.")
    c.msg(at(t0, minutes=6), "RCS", "INBOUND", "Thanks, please go ahead.")
    c.msg(at(t0, hours=2), "RCS", "OUTBOUND", "CHILLER-8821 is back to normal. How was your experience (1-5)?")
    c.msg(at(t0, hours=2, minutes=9), "RCS", "INBOUND", "4")
    items += c.items

    # 7) Closed ~9 days ago - CHILLER-8822 board swap on site (CSAT 5, SLA breach avoided, part approved)
    t0 = now - timedelta(days=9, hours=4)
    closed = t0 + timedelta(hours=3, minutes=40)
    c = _Case("SR-0B7D9C5E34", customer_id="CUST-1042", asset_id="CHILLER-8822", state="CLOSED",
              created_at=_iso(t0), priority="P2", restore_by=_iso(t0 + timedelta(hours=4)),
              correlation_id="corr-seed-0b7d9c5e", closed_at=_iso(closed), version=12)
    opened(c, _iso(t0), "Chiller supply temperature high")
    context(c, t0, "CUST-1042", "CHILLER-8822")
    c.tool(at(t0, minutes=8), "DiagnosticAgent", "executeSafeRemoteAction",
           {"assetId": "CHILLER-8822", "action": "controller_soft_reset"}, {"result": "NO_CHANGE"}, 1800)
    c.state(at(t0, minutes=15), "DIAGNOSING", "PLANNING_FIELD", "Remote fix not applicable")
    c.state(at(t0, minutes=20), "PLANNING_FIELD", "AWAITING_APPROVAL", "Part over threshold needs approval")
    c.state(at(t0, minutes=26), "AWAITING_APPROVAL", "PLANNING_FIELD", "Approval granted")
    c.state(at(t0, minutes=30), "PLANNING_FIELD", "SCHEDULED", "Field visit booked")
    c.state(at(t0, hours=2), "SCHEDULED", "IN_SERVICE", "Technician on site")
    c.evt(at(t0, hours=3, minutes=20), EVT_SLA_BREACH_AVOIDED, {"type": "SYSTEM", "name": "ServiceRecoveryAgent"},
          "SLA breach avoided", {"restoreBy": _iso(t0 + timedelta(hours=4)), "recoveredAt": at(t0, hours=3, minutes=20)})
    c.state(at(t0, hours=3, minutes=30), "IN_SERVICE", "VERIFYING", "Work order completed")
    c.state(_iso(closed), "VERIFYING", "CLOSED", "Verified healthy")
    c.evt(at(t0, hours=3, minutes=45), EVT_CSAT, {"type": "CUSTOMER", "name": "Anita Rao"}, "CSAT received: 5",
          {"score": 5})
    c.plan(1, [{"goal": "Replace control board", "expectedTools": ["findAndReservePart", "createServiceAppointment"]}],
           "Board swap after failed remote reset.")
    c.appt("WO-87001", "TECH-118", at(t0, hours=1, minutes=40), at(t0, hours=3, minutes=20), "COMPLETED")
    c.msg(at(t0, minutes=3), "RCS", "OUTBOUND", "Chiller alert received. We are diagnosing.")
    c.msg(at(t0, hours=3, minutes=35), "EMAIL", "OUTBOUND", "Subject: Service report for CHILLER-8822.")
    items += c.items
    approval(607, c, "PART_OVER_THRESHOLD:CTRL-BRD-900", "1240", "SERVICE_MANAGER", "APR-11", at(t0, minutes=20),
             status="APPROVED", reason="Within contract; critical cold chain asset.", decided_by="demo.approver",
             decided_at=at(t0, minutes=26))

    # 8) Cancelled ~5 days ago - AHU-9011 out-of-hours callout rejected (planned maintenance covers it)
    t0 = now - timedelta(days=5, hours=3)
    cancelled = t0 + timedelta(minutes=50)
    c = _Case(CANCELLED_AHU_ID, customer_id="CUST-1042", asset_id="AHU-9011", state="CANCELLED",
              created_at=_iso(t0), priority="P3", restore_by=_iso(t0 + timedelta(hours=24)),
              correlation_id="corr-seed-7e19d04b", closed_at=_iso(cancelled), version=5)
    opened(c, _iso(t0), "AHU supply fan vibration high")
    context(c, t0, "CUST-1042", "AHU-9011")
    c.tool(at(t0, minutes=4), "DiagnosticAgent", "getAssetHealth", {"assetId": "AHU-9011"},
           {"health": "WARNING", "alarm": "FAN_VIB_HIGH"}, 350)
    c.evt(at(t0, minutes=8), "APPROVAL_REQUESTED", {"type": "AGENT", "name": "SLAEntitlementAgent"},
          "Approval requested: out-of-hours technician visit", {"approvalId": "APR-0608-1"},
          tool_name="requestApproval", policy="REQUIRE_APPROVAL")
    c.state(at(t0, minutes=9), "DIAGNOSING", "AWAITING_APPROVAL", "Out-of-hours visit needs approval")
    c.state(_iso(cancelled), "AWAITING_APPROVAL", "CANCELLED",
            "Approval rejected: the fan is covered by the next planned maintenance visit")
    c.plan(1, [{"goal": "Check AHU-9011 supply fan", "expectedTools": ["requestApproval", "createServiceAppointment"]}],
           "Vibration alarm without airflow loss; visit only if approved out of hours.")
    c.msg(at(t0, minutes=10), "SMS", "OUTBOUND",
          "OpsResolve: AHU-9011 fan vibration alarm received. Checking whether an out-of-hours visit is needed.")
    c.msg(at(t0, minutes=51), "SMS", "OUTBOUND",
          "OpsResolve: no separate visit needed, the fan will be checked in the next planned maintenance. "
          "Request cancelled.")
    items += c.items
    approval(608, c, "AFTER_HOURS_VISIT", "0", "SERVICE_MANAGER", "APR-11", at(t0, minutes=8),
             status="REJECTED", reason="Covered by the next planned maintenance visit; no separate callout.",
             decided_by="demo.approver", decided_at=at(t0, minutes=48))

    # ===== S2 (meravix-broadband) closed cases for the KPI panel ====================================

    # 9) Closed ~6 days ago - ONT-55041 replaced by the customer (courier self-install), credit $25, CSAT 5
    t0 = now - timedelta(days=6, hours=3)
    closed = t0 + timedelta(hours=1, minutes=50)
    c = _Case(CLOSED_COURIER_ID, customer_id="CUST-2204", asset_id="ONT-55041", state="CLOSED",
              created_at=_iso(t0), priority="P3", restore_by=_iso(t0 + timedelta(hours=24)),
              correlation_id="corr-seed-3f8a62d1", closed_at=_iso(closed), version=14)
    opened(c, _iso(t0), "Customer reported - ONT-X200 (ONT-55041)", trigger_type="CUSTOMER_REPLY", channel="WHATSAPP")
    c.msg(_iso(t0), "WHATSAPP", "INBOUND", "My internet keeps dropping every few minutes.")
    c.msg(at(t0, seconds=6), "WHATSAPP", "OUTBOUND", "Thanks Ananya, we are checking your connection now.")
    context(c, t0, "CUST-2204", "ONT-55041")
    c.tool(at(t0, minutes=3), "DiagnosticAgent", "executeSafeRemoteAction",
           {"assetId": "ONT-55041", "action": "reboot_ont"}, {"result": "NO_CHANGE"}, 2100)
    c.state(at(t0, minutes=6), "DIAGNOSING", "PLANNING_FIELD", "Remote reboot ineffective")
    c.state(at(t0, minutes=12), "PLANNING_FIELD", "SCHEDULED", "Visit booked")
    c.state(at(t0, minutes=31), "SCHEDULED", "AT_RISK", "Part pick failed: ONT-X200 not at DEP-CHN-02")
    c.state(at(t0, minutes=41), "AT_RISK", "SCHEDULED", "Customer chose a courier delivery with self-install")
    c.state(at(t0, hours=1, minutes=20), "SCHEDULED", "IN_SERVICE", "Customer is installing the replacement ONT")
    c.state(at(t0, hours=1, minutes=25), "IN_SERVICE", "VERIFYING", "Customer installed the replacement ONT")
    c.evt(at(t0, hours=1, minutes=26), "SERVICE_CREDIT_APPLIED", {"type": "AGENT", "name": "ServiceRecoveryAgent"},
          "Applied a service credit of $25 for 5 h of downtime", {"downtimeHours": 5, "amountUsd": 25})
    c.evt(at(t0, hours=1, minutes=45), EVT_CSAT, {"type": "CUSTOMER", "name": "Ananya Shah"}, "CSAT received: 5",
          {"score": 5})
    c.state(_iso(closed), "VERIFYING", "CLOSED", "Verified healthy, customer feedback received")
    c.plan(1, [{"goal": "Restore ONT-55041", "expectedTools": ["findAndReservePart", "dispatchCourier"]}],
           "Part short at the depot: courier self-install, no visit needed.")
    c.msg(at(t0, minutes=42), "WHATSAPP", "OUTBOUND", "A courier is on the way with your new ONT (about 90 minutes).")
    c.msg(at(t0, hours=1, minutes=40), "WHATSAPP", "OUTBOUND",
          "Your line is back to normal. How was your experience (1-5)?")
    c.msg(at(t0, hours=1, minutes=45), "WHATSAPP", "INBOUND", "5")
    items += c.items

    # 10) Closed ~3 days ago - RTR-55022 replaced on site; the credit is above the cap and waits for approval
    t0 = now - timedelta(days=3, hours=14)
    closed = t0 + timedelta(hours=11)
    c = _Case(CLOSED_VISIT_ID, customer_id="CUST-2202", asset_id="RTR-55022", state="CLOSED",
              created_at=_iso(t0), priority="P3", restore_by=_iso(t0 + timedelta(hours=24)),
              correlation_id="corr-seed-6d2b90e7", closed_at=_iso(closed), version=13)
    opened(c, _iso(t0), "Customer reported - HGW-7 (RTR-55022)", trigger_type="CUSTOMER_REPLY", channel="WHATSAPP")
    c.msg(_iso(t0), "WHATSAPP", "INBOUND", "The router lights are all red and nothing connects.")
    c.msg(at(t0, seconds=7), "WHATSAPP", "OUTBOUND", "Thanks Karthik, we are checking your connection now.")
    context(c, t0, "CUST-2202", "RTR-55022")
    c.tool(at(t0, minutes=4), "DiagnosticAgent", "executeSafeRemoteAction",
           {"assetId": "RTR-55022", "action": "reprovision"}, {"result": "NO_CHANGE"}, 3100)
    c.state(at(t0, minutes=8), "DIAGNOSING", "PLANNING_FIELD", "Remote reprovision ineffective")
    c.state(at(t0, minutes=20), "PLANNING_FIELD", "SCHEDULED", "Visit booked")
    c.state(at(t0, hours=9), "SCHEDULED", "IN_SERVICE", "Technician on site")
    c.state(at(t0, hours=10), "IN_SERVICE", "VERIFYING", "Work order completed")
    c.evt(at(t0, hours=10, minutes=1), "SERVICE_CREDIT_PENDING_APPROVAL",
          {"type": "AGENT", "name": "ServiceRecoveryAgent"},
          "Service credit of $60 is above the $50 cap: approval requested", {"downtimeHours": 12, "amountUsd": 60})
    c.evt(at(t0, hours=10, minutes=30), EVT_CSAT, {"type": "CUSTOMER", "name": "Karthik Menon"}, "CSAT received: 4",
          {"score": 4})
    c.state(_iso(closed), "VERIFYING", "CLOSED", "Verified healthy, customer feedback received")
    c.plan(1, [{"goal": "Replace the home gateway", "expectedTools": ["createServiceAppointment"]}],
           "Remote reprovision failed; field swap of the gateway.")
    c.appt("WO-88103", "TEL-802", at(t0, hours=9), at(t0, hours=11), "COMPLETED")
    c.msg(at(t0, hours=10, minutes=25), "WHATSAPP", "OUTBOUND",
          "Your line is back to normal. How was your experience (1-5)?")
    c.msg(at(t0, hours=10, minutes=30), "WHATSAPP", "INBOUND", "4")
    items += c.items
    approval(609, c, "SERVICE_CREDIT_OVER_CAP:60", "60", "CARE_TEAM_LEAD", "APR-21", at(t0, hours=10, minutes=1))
    return items, approvals


def _case_tenant(case_id: str) -> str:
    return S2_TENANT if case_id in S2_CASE_IDS else DEFAULT_TENANT


def build_baseline(now: datetime | None = None, tenant_id: str = DEFAULT_TENANT) -> dict[str, list[dict]]:
    """Return ``{"cases": [...items...], "approvals": [...items...]}`` for one tenant (S1: 6 cases, 5 approvals;
    S2: 4 cases, 3 approvals). Every item carries ``tenantId``."""
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    cases, approvals = _cases(now)
    cases = [i for i in cases if _case_tenant(str(i["pk"]).removeprefix("CASE#")) == tenant_id]
    approvals = [a for a in approvals if _case_tenant(a["caseId"]) == tenant_id]
    metas = [i for i in cases if i["sk"] == "META"]
    missing = {m["caseId"] for m in metas} - set(CASE_DETAILS)
    assert not missing, f"CASE_DETAILS has no entry for {sorted(missing)}"
    for meta in metas:
        for key, value in CASE_DETAILS[meta["caseId"]].items():
            if value is not None:
                meta.setdefault(key, dict(value) if isinstance(value, dict) else value)
        if meta["caseId"] == CLOSED_COURIER_ID:
            meta["resolutionPath"] = "COURIER_SELF_INSTALL"
    for item in [*cases, *approvals]:
        item["tenantId"] = tenant_id
    return {"cases": cases, "approvals": approvals}


def summarize(baseline: dict[str, list[dict]]) -> dict[str, Any]:
    metas = [i for i in baseline["cases"] if i["sk"] == "META"]
    return {"cases": len(metas), "caseTableItems": len(baseline["cases"]),
            "approvals": len(baseline["approvals"]),
            "pending": sum(1 for a in baseline["approvals"] if a["status"] == "PENDING")}
