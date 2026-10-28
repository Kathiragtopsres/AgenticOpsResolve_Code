// =============================================================================
// OpsResolve Ops Console — API service layer
// -----------------------------------------------------------------------------

// The shared function is authHeaders() below, which builds the Bearer
// authorization header. Every endpoint makes its own fetch call and parses its
// own response separately — nothing else is shared.
// =============================================================================

import { getIdToken } from "./auth";
import { formatDateTime } from "@/lib/utils";

// -----------------------------------------------------------------------------
// Base configuration
// -----------------------------------------------------------------------------

export const BASE_URL = "https://o3sve2isd6.execute-api.us-east-1.amazonaws.com";

// -----------------------------------------------------------------------------
// Shared helper: authorization headers (the ONLY common function).
// Pass forceRefresh = true to force a refresh-token exchange (used for the
// one-time retry after a 401). Pass hasBody = true to add the JSON content-type.
// -----------------------------------------------------------------------------

export async function authHeaders(
  hasBody = false,
  forceRefresh = false,
): Promise<Record<string, string>> {
  const token = await getIdToken(forceRefresh);
  return {
    Authorization: `Bearer ${token}`,
    ...(hasBody ? { "Content-Type": "application/json" } : {}),
  };
}

// =============================================================================
// 2. Case Board
// =============================================================================



export type CasesSummary = {
  columns: Record<string, number>;
  total: number;
};

export async function getCasesSummary(): Promise<CasesSummary> {
  const headers = await authHeaders();
  let res = await fetch(`${BASE_URL}/cases/summary`, { headers });
  if (res.status === 401) {
    // Refresh once and retry once.
    const retryHeaders = await authHeaders(false, true);
    res = await fetch(`${BASE_URL}/cases/summary`, { headers: retryHeaders });
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    throw Object.assign(new Error(data?.error?.message ?? data?.message ?? res.statusText), {
      status: res.status,
      data,
    });
  }
  return {
    columns: data?.columns ?? {},
    total: typeof data?.total === "number" ? data.total : 0,
  };
}



export type BoardCard = {
  id: string; 
  customer: string; 
  issue: string; 
  asset: string; 
  column: string; 
  state: string; 
  priority: string; 
  sla: string; 
  channel: string; 
  opened: string; 
  breached: boolean; 
};


function formatSla(seconds: unknown): string {
  if (typeof seconds !== "number") return "NA";
  const sign = seconds < 0 ? "-" : "";
  const s = Math.abs(Math.floor(seconds));
  const hh = String(Math.floor(s / 3600)).padStart(2, "0");
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return `${sign}${hh}:${mm}:${ss}`;
}

export async function getCases(): Promise<BoardCard[]> {
  const headers = await authHeaders();
  let res = await fetch(`${BASE_URL}/cases?limit=100`, { headers });
  if (res.status === 401) {
    const retryHeaders = await authHeaders(false, true);
    res = await fetch(`${BASE_URL}/cases?limit=100`, { headers: retryHeaders });
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    throw Object.assign(new Error(data?.error?.message ?? data?.message ?? res.statusText), {
      status: res.status,
      data,
    });
  }
  const cases: any[] = Array.isArray(data?.cases) ? data.cases : [];
  return cases.map((c) => ({
    id: c?.caseId ?? "",
    state: c?.state ?? "",
    customer: c?.customerName ?? c?.customerId ?? "",
    issue: c?.title ?? "",
    asset: c?.assetId ?? "",
    column: c?.column ?? "",
    priority: c?.priority ?? "",
    sla: c?.sla ? formatSla(c.sla.secondsRemaining) : "",
    channel: c?.channel ?? "",
    opened: c?.createdAt ?? "",
    breached: !!c?.sla?.breached,
  }));
}

// =============================================================================
// 3. Recent / Search
// =============================================================================



export type CaseName = {
  id: string; 
  customer: string; 
  asset: string; 
  issue: string; 
};

async function fetchCaseNames(): Promise<CaseName[]> {
  const headers = await authHeaders();
  let res = await fetch(`${BASE_URL}/cases/names`, { headers });
  if (res.status === 401) {
    const retryHeaders = await authHeaders(false, true);
    res = await fetch(`${BASE_URL}/cases/names`, { headers: retryHeaders });
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    throw Object.assign(new Error(data?.error?.message ?? data?.message ?? res.statusText), {
      status: res.status,
      data,
    });
  }
  const cases: any[] = Array.isArray(data?.cases) ? data.cases : [];
  return cases.map((c) => ({
    id: c?.caseId ?? "",
    customer: c?.customerName ?? "",
    asset: c?.assetId ?? "",
    issue: c?.title ?? "",
  }));
}


let caseNamesPromise: Promise<CaseName[]> | null = null;

export function getCaseNames(): Promise<CaseName[]> {
  if (!caseNamesPromise) {
    caseNamesPromise = fetchCaseNames().catch((e) => {
      caseNamesPromise = null; // allow retry after a failure
      throw e;
    });
  }
  return caseNamesPromise;
}


// =============================================================================
// 4. Case Detail
// =============================================================================

// One case with everything the detail panel needs. GET /cases/{id}

export type CaseDetailData = {
  caseId: string;
  state: string;
  column: string;
  priority: string;
  customerId: string;
  customerName: string;
  assetId: string;
  title: string;
  channel: string;
  createdAt: string;
  closedAt: string;
  restoreBy: string;
  sla: string; 
  breached: boolean;
  category: string;
  subcategory: string;
  assignedQueue: string;
  caseOwner: string;
  customerDescription: string;
  sourceChannel: string;
  correlationId: string;
  plans: any[];
  appointments: any[];
  messages: any[];
  approvals: any[];
  attachments: any[];
  customer: any;
  asset: any;
  entitlement: any;
  requiredPart: any;
};

export async function getCaseById(id: string): Promise<CaseDetailData> {
  const path = `/cases/${encodeURIComponent(id)}`;
  const headers = await authHeaders();
  let res = await fetch(`${BASE_URL}${path}`, { headers });
  if (res.status === 401) {
    const retryHeaders = await authHeaders(false, true);
    res = await fetch(`${BASE_URL}${path}`, { headers: retryHeaders });
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    throw Object.assign(new Error(data?.error?.message ?? data?.message ?? res.statusText), {
      status: res.status,
      data,
    });
  }
  const c = data?.case ?? {};
  return {
    caseId: c?.caseId ?? "",
    state: c?.state ?? "",
    column: c?.column ?? "",
    priority: c?.priority ?? "",
    customerId: c?.customerId ?? "",
    customerName: c?.customerName ?? c?.customerId ?? "",
    assetId: c?.assetId ?? "",
    title: c?.title ?? "",
    channel: c?.channel ?? "",
    createdAt: c?.createdAt ?? "",
    closedAt: c?.closedAt ?? "",
    restoreBy: c?.restoreBy ?? "",
    sla:
      c?.sla && typeof c.sla.secondsRemaining === "number"
        ? formatSla(c.sla.secondsRemaining)
        : "",
    breached: !!c?.sla?.breached,
    category: c?.category ?? "",
    subcategory: c?.subcategory ?? "",
    assignedQueue: c?.assignedQueue ?? "",
    caseOwner: c?.caseOwner ?? "",
    customerDescription: c?.customerDescription ?? "",
    sourceChannel: c?.sourceChannel ?? "",
    correlationId: data?.correlationId ?? "",
    plans: Array.isArray(data?.plans) ? data.plans : [],
    appointments: Array.isArray(data?.appointments) ? data.appointments : [],
    messages: Array.isArray(data?.messages) ? data.messages : [],
    approvals: Array.isArray(data?.approvals) ? data.approvals : [],
    attachments: Array.isArray(data?.attachments) ? data.attachments : [],
    customer: data?.customer ?? null,
    asset: data?.asset ?? null,
    entitlement: data?.entitlement ?? null,
    requiredPart: data?.requiredPart ?? null,
  };
}

// =============================================================================
// 5. Timeline
// =============================================================================

export type TimelineEvent = {
  id: string; 
  caseId: string;
  time: string; 
  type: string; 
  title: string; 
  description: string; 
  source: string; 
};


function timelineBucket(apiType: unknown): string {
  const t = String(apiType ?? "").toUpperCase();
  if (t.includes("SLA") || t.includes("AT_RISK") || t.includes("BREACH")) return "alert";
  if (t.includes("APPROVAL")) return "policy";
  if (t.includes("TOOL") || t.includes("SPECIALIST") || t.includes("CSAT")) return "diagnosis";
  if (t.includes("CASE") || t.includes("STATE")) return "message";
  return "message";
}

function humanizeType(apiType: unknown): string {
  const t = String(apiType ?? "").trim();
  if (!t) return "NA";
  return t
    .toLowerCase()
    .split(/[_\s]+/)
    .map((w) => (w ? w.charAt(0).toUpperCase() + w.slice(1) : w))
    .join(" ");
}

function mapTimelineEvent(e: any): TimelineEvent {
  return {
    id: e?.eventId ?? "NA",
    caseId: e?.caseId ?? "NA",
    time: e?.ts ?? "NA",
    type: timelineBucket(e?.type),
    title: humanizeType(e?.type),
    description: e?.summary ?? "NA",
    source: e?.actor?.name ?? "NA",
  };
}

// Global activity feed, newest first. GET /timeline?limit=

export async function getTimeline(limit = 50): Promise<TimelineEvent[]> {
  const path = `/timeline?limit=${limit}`;
  const headers = await authHeaders();
  let res = await fetch(`${BASE_URL}${path}`, { headers });
  if (res.status === 401) {
    const retryHeaders = await authHeaders(false, true);
    res = await fetch(`${BASE_URL}${path}`, { headers: retryHeaders });
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    throw Object.assign(new Error(data?.error?.message ?? data?.message ?? res.statusText), {
      status: res.status,
      data,
    });
  }
  const events: any[] = Array.isArray(data?.events) ? data.events : [];
  return events.map(mapTimelineEvent);
}

// Per-case timeline, oldest first. GET /cases/{id}/timeline

export async function getCaseTimeline(id: string): Promise<TimelineEvent[]> {
  const path = `/cases/${encodeURIComponent(id)}/timeline`;
  const headers = await authHeaders();
  let res = await fetch(`${BASE_URL}${path}`, { headers });
  if (res.status === 401) {
    const retryHeaders = await authHeaders(false, true);
    res = await fetch(`${BASE_URL}${path}`, { headers: retryHeaders });
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    throw Object.assign(new Error(data?.error?.message ?? data?.message ?? res.statusText), {
      status: res.status,
      data,
    });
  }
  const events: any[] = Array.isArray(data?.events) ? data.events : [];
  return events.map(mapTimelineEvent);
}

// =============================================================================
// 6. Approvals
// =============================================================================


export type ApprovalItem = {
  id: string; 
  caseId: string;
  title: string; 
  context: string; 
  agent: string; 
  customer: string; 
  priority: string; 
  remaining: string; 
  amount: string; 
  status: string; 
  createdAt: string; 
  decision: string; 
};

function mapApproval(a: any): ApprovalItem {
  const decidedBits = [a?.decidedBy, formatDateTime(a?.decidedAt), a?.reason].filter(
    (x) => x !== null && x !== undefined && String(x).trim() !== "",
  );
  return {
    id: a?.approvalId ?? "NA",
    caseId: a?.caseId ?? "NA",
    title: a?.action ?? "NA",
    context: a?.caseTitle ?? "NA",
    agent: a?.requestedBy ?? a?.approverRole ?? "NA",
    customer: a?.customerName ?? "NA",
    priority: a?.casePriority ?? "NA",
    remaining:
      a?.sla && typeof a.sla.secondsRemaining === "number"
        ? formatSla(a.sla.secondsRemaining)
        : "NA",
    amount: a?.amount ?? "NA",
    status: a?.status ?? "NA",
    createdAt: formatDateTime(a?.createdAt),
    decision: decidedBits.length ? decidedBits.join(" · ") : "",
  };
}

// GET /approvals?status= (PENDING | DECIDED; omit for all). Newest first.

export async function getApprovals(status?: "PENDING" | "DECIDED"): Promise<ApprovalItem[]> {
  const path = status ? `/approvals?status=${status}` : `/approvals`;
  const headers = await authHeaders();
  let res = await fetch(`${BASE_URL}${path}`, { headers });
  if (res.status === 401) {
    const retryHeaders = await authHeaders(false, true);
    res = await fetch(`${BASE_URL}${path}`, { headers: retryHeaders });
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    throw Object.assign(new Error(data?.error?.message ?? data?.message ?? res.statusText), {
      status: res.status,
      data,
    });
  }
  const approvals: any[] = Array.isArray(data?.approvals) ? data.approvals : [];
  return approvals.map(mapApproval);
}


export async function decideApproval(
  id: string,
  decision: "APPROVE" | "REJECT",
  reason?: string,
): Promise<ApprovalItem> {
  const path = `/approvals/${encodeURIComponent(id)}/decision`;
  const body: { decision: "APPROVE" | "REJECT"; reason?: string } = { decision };
  if (reason && reason.trim()) body.reason = reason.trim();
  const headers = await authHeaders(true);
  let res = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (res.status === 401) {
    const retryHeaders = await authHeaders(true, true);
    res = await fetch(`${BASE_URL}${path}`, {
      method: "POST",
      headers: retryHeaders,
      body: JSON.stringify(body),
    });
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    throw Object.assign(new Error(data?.error?.message ?? data?.message ?? res.statusText), {
      status: res.status,
      data,
    });
  }
  return mapApproval(data);
}

// =============================================================================
// 7. KPIs
// =============================================================================

// GET /kpis?weeks=  to populate the kpis page

export type KpiData = {
  weeks: number;
  windowStart: string;
  mttr: { minutes: number | null; casesClosed: number; trend: any[] };
  slaBreachesAvoided: { total: number; trend: any[] };
  remoteResolutionRate: { rate: number | null; remoteResolved: number; resolved: number };
  csat: { average: number | null; responses: number };
  casesByChannel: Record<string, number>;
};

export async function getKpis(weeks = 8): Promise<KpiData> {
  const path = `/kpis?weeks=${weeks}`;
  const headers = await authHeaders();
  let res = await fetch(`${BASE_URL}${path}`, { headers });
  if (res.status === 401) {
    const retryHeaders = await authHeaders(false, true);
    res = await fetch(`${BASE_URL}${path}`, { headers: retryHeaders });
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    throw Object.assign(new Error(data?.error?.message ?? data?.message ?? res.statusText), {
      status: res.status,
      data,
    });
  }
  return {
    weeks: typeof data?.weeks === "number" ? data.weeks : weeks,
    windowStart: data?.windowStart ?? "NA",
    mttr: {
      minutes: typeof data?.mttr?.minutes === "number" ? data.mttr.minutes : null,
      casesClosed: data?.mttr?.casesClosed ?? 0,
      trend: Array.isArray(data?.mttr?.trend) ? data.mttr.trend : [],
    },
    slaBreachesAvoided: {
      total: typeof data?.slaBreachesAvoided?.total === "number" ? data.slaBreachesAvoided.total : 0,
      trend: Array.isArray(data?.slaBreachesAvoided?.trend) ? data.slaBreachesAvoided.trend : [],
    },
    remoteResolutionRate: {
      rate:
        typeof data?.remoteResolutionRate?.rate === "number"
          ? data.remoteResolutionRate.rate
          : null,
      remoteResolved: data?.remoteResolutionRate?.remoteResolved ?? 0,
      resolved: data?.remoteResolutionRate?.resolved ?? 0,
    },
    csat: {
      average: typeof data?.csat?.average === "number" ? data.csat.average : null,
      responses: data?.csat?.responses ?? 0,
    },
    casesByChannel:
      data?.casesByChannel && typeof data.casesByChannel === "object" ? data.casesByChannel : {},
  };
}

// GET /cases?recent=N  The N newest cases for the "recent cases" widget.

export type RecentCase = {
  id: string; 
  customer: string; 
  state: string; 
  channel: string; 
  sla: string; 
};

export async function getRecentCases(n = 5): Promise<RecentCase[]> {
  const path = `/cases?recent=${n}`;
  const headers = await authHeaders();
  let res = await fetch(`${BASE_URL}${path}`, { headers });
  if (res.status === 401) {
    const retryHeaders = await authHeaders(false, true);
    res = await fetch(`${BASE_URL}${path}`, { headers: retryHeaders });
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    throw Object.assign(new Error(data?.error?.message ?? data?.message ?? res.statusText), {
      status: res.status,
      data,
    });
  }
  const cases: any[] = Array.isArray(data?.cases) ? data.cases : [];
  return cases.map((c) => ({
    id: c?.caseId ?? "NA",
    customer: c?.customerName ?? c?.customerId ?? "NA",
    state: c?.column ?? c?.state ?? "NA",
    channel: c?.channel ?? "NA",
    sla: c?.sla ? formatSla(c.sla.secondsRemaining) : "NA",
  }));
}

// =============================================================================
// 8. Scenario Controller
// =============================================================================

// All scenario endpoints share one response shape:
// { ok, logs: [{ ts, level, message }], result | error }.

export type ScenarioLogLine = { ts: string; level: string; message: string };
export type ScenarioResponse = {
  ok: boolean;
  logs: ScenarioLogLine[];
  result?: any;
  error?: { code: string; message: string };
};

function parseScenario(data: any, res: Response): ScenarioResponse {
  if (!res.ok && !data) {
    throw Object.assign(new Error(res.statusText), { status: res.status });
  }
  return {
    ok: !!data?.ok,
    logs: Array.isArray(data?.logs) ? data.logs : [],
    result: data?.result,
    error: data?.error,
  };
}

// GET /scenario/telemetry/profiles — read-only; feeds the telemetry dropdown.

export type TelemetryProfile = {
  id: string;
  label: string;
  description: string;
  assetId: string;
  opensCase: boolean;
};
export type TelemetryProfiles = {
  profiles: TelemetryProfile[];
  defaultProfileId: string;
};

export async function getTelemetryProfiles(): Promise<TelemetryProfiles> {
  const headers = await authHeaders();
  let res = await fetch(`${BASE_URL}/scenario/telemetry/profiles`, { headers });
  if (res.status === 401) {
    const retryHeaders = await authHeaders(false, true);
    res = await fetch(`${BASE_URL}/scenario/telemetry/profiles`, { headers: retryHeaders });
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    throw Object.assign(new Error(data?.error?.message ?? data?.message ?? res.statusText), {
      status: res.status,
      data,
    });
  }
  const profiles: any[] = Array.isArray(data?.profiles) ? data.profiles : [];
  return {
    profiles: profiles.map((p) => ({
      id: p?.id ?? "",
      label: p?.label ?? p?.id ?? "NA",
      description: p?.description ?? "",
      assetId: p?.assetId ?? "",
      opensCase: !!p?.opensCase,
    })),
    defaultProfileId: data?.defaultProfileId ?? (profiles[0]?.id ?? ""),
  };
}

// POST /scenario/reset — deletes all data and restores the demo baseline.

export async function scenarioReset(): Promise<ScenarioResponse> {
  const headers = await authHeaders(true);
  let res = await fetch(`${BASE_URL}/scenario/reset`, { method: "POST", headers });
  if (res.status === 401) {
    const retryHeaders = await authHeaders(true, true);
    res = await fetch(`${BASE_URL}/scenario/reset`, { method: "POST", headers: retryHeaders });
  }
  const data = await res.json().catch(() => null);
  return parseScenario(data, res);
}

// POST /scenario/telemetry — body { profile, assetId? }.

export async function scenarioTelemetry(
  profile: string,
  assetId?: string,
): Promise<ScenarioResponse> {
  const body: { profile: string; assetId?: string } = { profile };
  if (assetId && assetId.trim()) body.assetId = assetId.trim();
  const headers = await authHeaders(true);
  let res = await fetch(`${BASE_URL}/scenario/telemetry`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (res.status === 401) {
    const retryHeaders = await authHeaders(true, true);
    res = await fetch(`${BASE_URL}/scenario/telemetry`, {
      method: "POST",
      headers: retryHeaders,
      body: JSON.stringify(body),
    });
  }
  const data = await res.json().catch(() => null);
  return parseScenario(data, res);
}

// Shared helper to POST an optional { caseId } body to a scenario event route.

async function postScenarioEvent(path: string, caseId?: string): Promise<ScenarioResponse> {
  const hasBody = !!(caseId && caseId.trim());
  const buildInit = (headers: Record<string, string>): RequestInit =>
    hasBody
      ? { method: "POST", headers, body: JSON.stringify({ caseId }) }
      : { method: "POST", headers };
  let res = await fetch(`${BASE_URL}${path}`, buildInit(await authHeaders(hasBody)));
  if (res.status === 401) {
    res = await fetch(`${BASE_URL}${path}`, buildInit(await authHeaders(hasBody, true)));
  }
  const data = await res.json().catch(() => null);
  return parseScenario(data, res);
}

// POST /scenario/technician-delay — body { caseId? } .
export async function scenarioTechnicianDelay(caseId?: string): Promise<ScenarioResponse> {
  return postScenarioEvent("/scenario/technician-delay", caseId);
}

// POST /scenario/part-shortage — body { caseId? }.
export async function scenarioPartShortage(caseId?: string): Promise<ScenarioResponse> {
  return postScenarioEvent("/scenario/part-shortage", caseId);
}

// POST /scenario/complete-work-order — body { caseId? }.
export async function scenarioCompleteWorkOrder(caseId?: string): Promise<ScenarioResponse> {
  return postScenarioEvent("/scenario/complete-work-order", caseId);
}

// =============================================================================
// 9. Trace Viewer
// =============================================================================

// Flat span shape the Trace Viewer .
export type TraceMarker = {
  ts: string; 
  type: string; 
  summary: string; 
};
export type TraceSpan = {
  id: string; 
  parent?: string; 
  name: string;
  description: string;
  startedAt: string; 
  duration: string; 
  status: string; 
  policyDecision: string; 
  type: string; 
  payload: unknown; 
  markers: TraceMarker[]; 
};
export type TraceData = {
  caseId: string;
  spanCount: number;
  startedAt: string; 
  spans: TraceSpan[]; 
};

function formatLatency(ms: unknown): string {
  if (typeof ms !== "number") return "NA";
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(ms % 1000 === 0 ? 0 : 1)}s`;
}

// Walk the nested trace tree depth-first into a flat list (parents first).
function flattenSpan(node: any, out: TraceSpan[]): void {
  if (!node) return;
  const markers: TraceMarker[] = Array.isArray(node?.markers)
    ? node.markers.map((m: any) => ({
        ts: m?.ts ?? "NA",
        type: m?.type ?? "NA",
        summary: m?.summary ?? "NA",
      }))
    : [];
  out.push({
    id: node?.spanId ?? "NA",
    parent: node?.parentId ?? undefined,
    name: node?.name ?? "NA",
    description: node?.actor?.name ? String(node.actor.name) : (node?.kind ?? "NA"),
    startedAt: node?.startedAt ?? "NA",
    duration: formatLatency(node?.latencyMs),
    status: String(node?.status ?? "").toUpperCase() === "ERROR" ? "error" : "success",
    policyDecision: node?.policyDecision ?? "NA",
    type: String(node?.kind ?? "").toLowerCase() || "NA",
    payload:
      node?.input !== undefined || node?.output !== undefined
        ? { input: node?.input ?? null, output: node?.output ?? null }
        : "NA",
    markers,
  });
  const children = Array.isArray(node?.children) ? node.children : [];
  for (const child of children) flattenSpan(child, out);
}

// GET /cases/{id}/trace — span tree for the Trace Viewer.
export async function getCaseTrace(id: string): Promise<TraceData> {
  const path = `/cases/${encodeURIComponent(id)}/trace`;
  const headers = await authHeaders();
  let res = await fetch(`${BASE_URL}${path}`, { headers });
  if (res.status === 401) {
    const retryHeaders = await authHeaders(false, true);
    res = await fetch(`${BASE_URL}${path}`, { headers: retryHeaders });
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    throw Object.assign(new Error(data?.error?.message ?? data?.message ?? res.statusText), {
      status: res.status,
      data,
    });
  }
  const spans: TraceSpan[] = [];
  if (data?.trace) flattenSpan(data.trace, spans);
  // Trace start time: the earliest span's startedAt (the root CASE span has none,
  // so fall back to the first child/orchestrator that carries one).
  const startedAt = spans.find((s) => s.startedAt && s.startedAt !== "NA")?.startedAt ?? "NA";
  return {
    caseId: data?.caseId ?? id,
    spanCount: typeof data?.spanCount === "number" ? data.spanCount : spans.length,
    startedAt,
    spans,
  };
}
