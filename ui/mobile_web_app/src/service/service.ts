// =============================================================================
// OpsResolve Field - API service layer
// -----------------------------------------------------------------------------


import { getIdToken } from "@/lib/auth";

// Base URL for every field API route.
export const API_BASE_URL = "https://o3sve2isd6.execute-api.us-east-1.amazonaws.com/mobile-web-app";

// -----------------------------------------------------------------------------
// Shared: authorization headers.
// This is the shared helper. It builds the headers added to each request.
//
// The API routes are protected by a Cognito JWT authorizer (the gateway
// responds with `www-authenticate: Bearer` on a 401), so every request must
// carry the Cognito id token as a bearer token.
// -----------------------------------------------------------------------------
export async function authHeaders(extra?: Record<string, string>): Promise<Record<string, string>> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(extra || {}),
  };
  const token = await getIdToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return headers;
}

// =============================================================================
// API #1 - GET /me
// Page: Home greeting/avatar, Profile header + stats, Certifications & Skills tabs
// =============================================================================

export type MeCertification = { model: string | null; validUntil: string | null };
export type MeSkill = { skill: string | null; level: number | null };
export type MeStats = {
  jobsThisWeek: number | null;
  firstTimeFixRate: number | null;
  rating: number | null;
};
export type MeResponse = {
  technicianId?: string | null;
  username?: string | null;
  name?: string | null;
  initials?: string | null;
  title?: string | null;
  region?: string | null;
  shift?: string | null;
  phone?: string | null;
  homeDepotId?: string | null;
  certifications?: MeCertification[] | null;
  skills?: MeSkill[] | null;
  stats?: MeStats | null;
};

export async function getMe(technicianId: string): Promise<MeResponse> {
  const url = `${API_BASE_URL}/me?technicianId=${encodeURIComponent(technicianId)}`;
  const response = await fetch(url, {
    method: "GET",
    headers: await authHeaders(),
  });
  if (!response.ok) {
    throw new Error(`GET /me failed with status ${response.status}`);
  }
  const data = (await response.json()) as MeResponse;
  return data;
}

// =============================================================================
// API #2 - GET /jobs?technicianId={id}&view={view}
// Page: Home "Today's jobs" + counters, My Jobs tabs, Profile "Recent Jobs"
// view is one of: 'today' | 'upcoming' | 'completed'
// =============================================================================

export type JobsView = "today" | "upcoming" | "completed";

export type JobListItem = {
  jobId?: string | null;
  workOrderId?: string | null;
  customerName?: string | null;
  siteName?: string | null;
  address?: string | null;
  assetLabel?: string | null;
  slotStart?: string | null;
  slotEnd?: string | null;
  priority?: string | null;
  jobStatus?: string | null;
  distanceKm?: number | null;
  completedAt?: string | null;
  rating?: number | null;
};

export type JobsResponse = {
  view?: string | null;
  jobs?: JobListItem[] | null;
  counts?: { today?: number | null; completed?: number | null } | null;
  nextStart?: string | null;
};

export async function getJobs(
  technicianId: string,
  view: JobsView,
): Promise<JobsResponse> {
  const url =
    `${API_BASE_URL}/jobs?technicianId=${encodeURIComponent(technicianId)}` +
    `&view=${encodeURIComponent(view)}`;
  const response = await fetch(url, {
    method: "GET",
    headers: await authHeaders(),
  });
  if (!response.ok) {
    throw new Error(`GET /jobs failed with status ${response.status}`);
  }
  const data = (await response.json()) as JobsResponse;
  return data;
}

// =============================================================================
// Availability slots - technician self-set availability shown on the Calendar.
// -----------------------------------------------------------------------------
// addAvailability() calls the real POST /availability endpoint. get/delete have
// no endpoint yet and still persist to localStorage so the Calendar keeps
// working end-to-end; replace their bodies when those routes are available.
// =============================================================================

// Error carrying the HTTP status so callers can show status-specific messages
// (e.g. 409 = slot BOOKED and cannot be deleted, 404 = not found).
export class AvailabilityError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "AvailabilityError";
    this.status = status;
  }
}

export type AvailabilityStatus = "AVAILABLE" | "BOOKED";

export type AvailabilitySlot = {
  slotId: string;
  technicianId: string;
  date: string; // "YYYY-MM-DD"
  start: string; // "HH:mm" 24h
  end: string; // "HH:mm" 24h
  note?: string | null;
  createdAt?: string | null; // ISO timestamp
  status?: string | null; // "AVAILABLE" | "BOOKED"
  appointmentId?: string | null;
};

// GET availability slots for one technician / one day, filtered by status.
// GET /availability?technicianId={id}&date={YYYY-MM-DD}&status={AVAILABLE|BOOKED}
export async function getAvailability(
  technicianId: string,
  date: string,
  status: AvailabilityStatus,
): Promise<AvailabilitySlot[]> {
  const url =
    `${API_BASE_URL}/availability?technicianId=${encodeURIComponent(technicianId)}` +
    `&date=${encodeURIComponent(date)}&status=${encodeURIComponent(status)}`;
  const response = await fetch(url, {
    method: "GET",
    headers: await authHeaders(),
  });
  if (!response.ok) {
    throw new Error(`GET /availability failed with status ${response.status}`);
  }
  const data = (await response.json().catch(() => [])) as AvailabilitySlot[];
  return Array.isArray(data) ? data : [];
}

// ADD an availability slot via POST /availability.
export type AddAvailabilityResponse = {
  slotId?: string | null;
  technicianId?: string | null;
  date?: string | null;
  start?: string | null;
  end?: string | null;
  note?: string | null;
  createdAt?: string | null;
};

export async function addAvailability(
  technicianId: string,
  slot: { date: string; start: string; end: string; note?: string | null },
  idempotencyKey: string = crypto.randomUUID(),
): Promise<AvailabilitySlot> {
  const url = `${API_BASE_URL}/availability`;
  const response = await fetch(url, {
    method: "POST",
    headers: await authHeaders(),
    body: JSON.stringify({
      technicianId,
      date: slot.date,
      start: slot.start,
      end: slot.end,
      note: slot.note ?? "",
      idempotencyKey,
    }),
  });
  if (!response.ok) {
    throw new Error(`POST /availability failed with status ${response.status}`);
  }
  const data = (await response.json().catch(() => ({}))) as AddAvailabilityResponse;
  return {
    slotId: data.slotId ?? crypto.randomUUID(),
    technicianId: data.technicianId ?? technicianId,
    date: data.date ?? slot.date,
    start: data.start ?? slot.start,
    end: data.end ?? slot.end,
    note: data.note ?? slot.note ?? null,
    createdAt: data.createdAt ?? new Date().toISOString(),
    status: "AVAILABLE",
  };
}

// DELETE an availability slot via DELETE /availability/{slotId}?technicianId={id}.
export async function deleteAvailability(
  technicianId: string,
  slotId: string,
): Promise<void> {
  const url =
    `${API_BASE_URL}/availability/${encodeURIComponent(slotId)}` +
    `?technicianId=${encodeURIComponent(technicianId)}`;
  const response = await fetch(url, {
    method: "DELETE",
    headers: await authHeaders(),
  });
  // Success is a 200 with an empty JSON body. 404 = not found / not owned,
  // 409 = slot is BOOKED and cannot be deleted. Surface the status so the UI
  // can show a specific message.
  if (!response.ok) {
    throw new AvailabilityError(
      `DELETE /availability/{slotId} failed with status ${response.status}`,
      response.status,
    );
  }
}

// =============================================================================
// Slot counts - per-day available/booked counts for the Calendar month view.
// POST /slot_count  body: { technician_id, date }  -> array of day counts from
// `date` onward. Called with the first visible day of the month being viewed.
// =============================================================================

export type SlotCountDay = {
  date: string; // "YYYY-MM-DD"
  available_slot_count: number;
  booked_slot_count: number;
};

export async function getSlotCounts(
  technicianId: string,
  date: string,
): Promise<SlotCountDay[]> {
  const url = `${API_BASE_URL}/slot_count`;
  const response = await fetch(url, {
    method: "POST",
    headers: await authHeaders(),
    body: JSON.stringify({ technician_id: technicianId, date }),
  });
  if (!response.ok) {
    throw new Error(`POST /slot_count failed with status ${response.status}`);
  }
  const data = (await response.json().catch(() => [])) as SlotCountDay[];
  return Array.isArray(data) ? data : [];
}

// =============================================================================
// API #3 - GET /jobs/{jobId}?technicianId={id}
// Page: Job Detail screen, Navigation screen, Completion checklist
// =============================================================================

export type JobCustomer = { customerId?: string | null; name?: string | null };
export type JobContact = {
  contactId?: string | null;
  name?: string | null;
  role?: string | null;
  phone?: string | null;
};
export type JobSite = {
  siteId?: string | null;
  name?: string | null;
  address?: string | null;
  timezone?: string | null;
  lat?: number | null;
  lon?: number | null;
};
export type JobAsset = {
  assetId?: string | null;
  displayName?: string | null;
  model?: string | null;
  serial?: string | null;
};
export type JobBrief = { text?: string | null; confidence?: number | null };
export type JobDiagnosis = { at?: string | null; summary?: string | null };
export type JobRequiredPart = {
  partId?: string | null;
  name?: string | null;
  qty?: number | null;
};
export type JobChecklistItem = { item?: string | null; done?: boolean | null };
export type JobManualRef = { title?: string | null; excerpt?: string | null };

export type JobDetailResponse = {
  jobId?: string | null;
  workOrderId?: string | null;
  caseState?: string | null;
  jobStatus?: string | null;
  priority?: string | null;
  slotStart?: string | null;
  slotEnd?: string | null;
  customer?: JobCustomer | null;
  contact?: JobContact | null;
  site?: JobSite | null;
  asset?: JobAsset | null;
  safetyNote?: string | null;
  brief?: JobBrief | null;
  symptoms?: string | null;
  diagnosisHistory?: JobDiagnosis[] | null;
  requiredParts?: JobRequiredPart[] | null;
  checklist?: JobChecklistItem[] | null;
  manualRef?: JobManualRef | null;
  partsUsed?: unknown[] | null;
};

export async function getJobDetail(
  technicianId: string,
  jobId: string,
): Promise<JobDetailResponse> {
  const url =
    `${API_BASE_URL}/jobs/${encodeURIComponent(jobId)}` +
    `?technicianId=${encodeURIComponent(technicianId)}`;
  const response = await fetch(url, {
    method: "GET",
    headers: await authHeaders(),
  });
  if (!response.ok) {
    throw new Error(`GET /jobs/{jobId} failed with status ${response.status}`);
  }
  const data = (await response.json()) as JobDetailResponse;
  return data;
}

// =============================================================================
// API #4 - POST /jobs/{jobId}/status?technicianId={id}
// Page: Job Detail "Start Navigation" (-> EN_ROUTE),
//       Navigation "Arrived on Site" (-> ON_SITE)
// =============================================================================

export type JobStatusValue = "EN_ROUTE" | "ON_SITE";

export type JobStatusResponse = {
  jobId?: string | null;
  jobStatus?: string | null;
  caseState?: string | null;
  customerNotified?: { messageId?: string | null; channel?: string | null } | null;
};

export async function postJobStatus(
  technicianId: string,
  jobId: string,
  status: JobStatusValue,
  idempotencyKey: string = crypto.randomUUID(),
): Promise<JobStatusResponse> {
  const url =
    `${API_BASE_URL}/jobs/${encodeURIComponent(jobId)}/status` +
    `?technicianId=${encodeURIComponent(technicianId)}`;
  const response = await fetch(url, {
    method: "POST",
    headers: await authHeaders(),
    body: JSON.stringify({ status, idempotencyKey }),
  });
  if (!response.ok) {
    throw new Error(`POST /jobs/{jobId}/status failed with status ${response.status}`);
  }
  const data = (await response.json()) as JobStatusResponse;
  return data;
}

// =============================================================================
// API #5 - GET /depot-stock?technicianId={id}&jobId={jobId}
// Page: Parts & Inventory screen (required parts card + depot stock list), part picker
// =============================================================================

export type DepotInfo = { depotId?: string | null; name?: string | null };

export type DepotRequiredPart = {
  partId?: string | null;
  name?: string | null;
  qtyRequired?: number | null;
  qtyRemaining?: number | null;
  onHand?: number | null;
  reserved?: number | null;
  available?: number | null;
  stockLevel?: string | null;
};

export type DepotStockItem = {
  partId?: string | null;
  name?: string | null;
  description?: string | null;
  onHand?: number | null;
  reserved?: number | null;
  available?: number | null;
  stockLevel?: string | null;
};

export type DepotStockResponse = {
  depot?: DepotInfo | null;
  requiredParts?: DepotRequiredPart[] | null;
  items?: DepotStockItem[] | null;
};

export async function getDepotStock(
  technicianId: string,
  jobId: string,
): Promise<DepotStockResponse> {
  const url =
    `${API_BASE_URL}/depot-stock?technicianId=${encodeURIComponent(technicianId)}` +
    `&jobId=${encodeURIComponent(jobId)}`;
  const response = await fetch(url, {
    method: "GET",
    headers: await authHeaders(),
  });
  if (!response.ok) {
    throw new Error(`GET /depot-stock failed with status ${response.status}`);
  }
  const data = (await response.json()) as DepotStockResponse;
  return data;
}

// =============================================================================
// API #6 - POST /jobs/{jobId}/parts/used?technicianId={id}
// Page: Parts screen "Mark Part Used"
// =============================================================================

export type PartUsedResponse = {
  jobId?: string | null;
  partId?: string | null;
  qty?: number | null;
  usedAt?: string | null;
  depotId?: string | null;
};

export async function postPartUsed(
  technicianId: string,
  jobId: string,
  partId: string,
  qty: number,
  idempotencyKey: string = crypto.randomUUID(),
): Promise<PartUsedResponse> {
  const url =
    `${API_BASE_URL}/jobs/${encodeURIComponent(jobId)}/parts/used` +
    `?technicianId=${encodeURIComponent(technicianId)}`;
  const response = await fetch(url, {
    method: "POST",
    headers: await authHeaders(),
    body: JSON.stringify({ partId, qty, idempotencyKey }),
  });
  if (!response.ok) {
    throw new Error(`POST /jobs/{jobId}/parts/used failed with status ${response.status}`);
  }
  const data = (await response.json()) as PartUsedResponse;
  return data;
}

// =============================================================================
// API #7 - POST /jobs/{jobId}/parts/requests?technicianId={id}
// Page: Parts screen "Request Additional Part" sheet
// =============================================================================

export type PartRequestResponse = {
  action?: string | null;
  caseId?: string | null;
  approvalId?: string | null;
  approverRole?: string | null;
  amount?: string | null;
  notifiedApprover?: string | null;
  status?: string | null;
  createdAt?: string | null;
};

export async function postPartRequest(
  technicianId: string,
  jobId: string,
  partId: string,
  qty: number,
  note: string = "",
  idempotencyKey: string = crypto.randomUUID(),
): Promise<PartRequestResponse> {
  const url =
    `${API_BASE_URL}/jobs/${encodeURIComponent(jobId)}/parts/requests` +
    `?technicianId=${encodeURIComponent(technicianId)}`;
  const response = await fetch(url, {
    method: "POST",
    headers: await authHeaders(),
    body: JSON.stringify({ partId, qty, note, idempotencyKey }),
  });
  if (!response.ok) {
    throw new Error(`POST /jobs/{jobId}/parts/requests failed with status ${response.status}`);
  }
  const data = (await response.json()) as PartRequestResponse;
  return data;
}

// =============================================================================
// API #8 - GET /parts/{partId}?technicianId={id}
// Page: Parts screen "Scan barcode" (optional, low priority)
// =============================================================================

export type PartLookupResponse = {
  partId?: string | null;
  name?: string | null;
  description?: string | null;
  unitValueUsd?: number | null;
  compatibleModels?: string[] | null;
  depotStock?: {
    depotId?: string | null;
    onHand?: number | null;
    available?: number | null;
  } | null;
};

export async function getPartByBarcode(
  technicianId: string,
  partId: string,
): Promise<PartLookupResponse> {
  const url =
    `${API_BASE_URL}/parts/${encodeURIComponent(partId)}` +
    `?technicianId=${encodeURIComponent(technicianId)}`;
  const response = await fetch(url, {
    method: "GET",
    headers: await authHeaders(),
  });
  if (!response.ok) {
    throw new Error(`GET /parts/{partId} failed with status ${response.status}`);
  }
  const data = (await response.json()) as PartLookupResponse;
  return data;
}

// =============================================================================
// API #9 - POST /uploads?technicianId={id}
// Page: Completion "Add Photo", customer signature canvas; Assistant camera button
// Returns a presigned S3 URL; the caller must then PUT the file bytes to it.
// =============================================================================

export type UploadKind = "PHOTO" | "SIGNATURE";

export type UploadRequestBody = {
  jobId: string;
  kind: UploadKind;
  contentType: string;
  sizeBytes: number;
};

export type UploadResponse = {
  key?: string | null;
  uploadUrl?: string | null;
  expiresInSeconds?: number | null;
  requiredHeaders?: Record<string, string> | null;
};

export async function postUpload(
  technicianId: string,
  body: UploadRequestBody,
): Promise<UploadResponse> {
  const url = `${API_BASE_URL}/uploads?technicianId=${encodeURIComponent(technicianId)}`;
  const response = await fetch(url, {
    method: "POST",
    headers: await authHeaders(),
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`POST /uploads failed with status ${response.status}`);
  }
  const data = (await response.json()) as UploadResponse;
  return data;
}

// Convenience for API #9: request a presigned URL and PUT the file bytes to S3.
// Resolves with the S3 object key (used later by complete / assistant calls).
export async function uploadFile(
  technicianId: string,
  jobId: string,
  kind: UploadKind,
  file: File,
): Promise<string> {
  const presigned = await postUpload(technicianId, {
    jobId,
    kind,
    contentType: file.type || "application/octet-stream",
    sizeBytes: file.size,
  });
  if (!presigned.uploadUrl || !presigned.key) {
    throw new Error("Upload URL or key missing from /uploads response");
  }
  const putResponse = await fetch(presigned.uploadUrl, {
    method: "PUT",
    headers: presigned.requiredHeaders ?? { "Content-Type": file.type },
    body: file,
  });
  if (!putResponse.ok) {
    throw new Error(`S3 upload failed with status ${putResponse.status}`);
  }
  return presigned.key;
}

// =============================================================================
// API #10 - POST /jobs/{jobId}/complete?technicianId={id}
// Page: Job Completion screen -> "Complete this job?" confirm
// =============================================================================

export type CompleteChecklistItem = { item: string; done: boolean };

export type CompleteJobBody = {
  checklist: CompleteChecklistItem[];
  photoKeys: string[];
  signatureKey: string | null;
  debrief: string;
};

export type CompleteJobResponse = {
  jobId?: string | null;
  workOrderId?: string | null;
  jobStatus?: string | null;
  completedAt?: string | null;
  eventId?: string | null;
};

export async function postJobComplete(
  technicianId: string,
  jobId: string,
  body: CompleteJobBody,
  idempotencyKey: string = crypto.randomUUID(),
): Promise<CompleteJobResponse> {
  const url =
    `${API_BASE_URL}/jobs/${encodeURIComponent(jobId)}/complete` +
    `?technicianId=${encodeURIComponent(technicianId)}`;
  const response = await fetch(url, {
    method: "POST",
    headers: await authHeaders(),
    body: JSON.stringify({ ...body, idempotencyKey }),
  });
  if (!response.ok) {
    throw new Error(`POST /jobs/{jobId}/complete failed with status ${response.status}`);
  }
  const data = (await response.json()) as CompleteJobResponse;
  return data;
}

// =============================================================================
// API #11 - POST /assistant/messages?technicianId={id}
// Page: Knowledge Assistant screen (text question, optional photo, citation chips)
// =============================================================================

export type AssistantHistoryItem = { sender: string; text: string };

export type AssistantMessageBody = {
  jobId: string | null;
  text: string;
  imageKey: string | null;
  history: AssistantHistoryItem[];
};

export type AssistantCitation = {
  title?: string | null;
  source?: string | null;
  excerpt?: string | null;
  score?: number | null;
};

export type AssistantMessageResponse = {
  answer?: string | null;
  citations?: AssistantCitation[] | null;
  modelId?: string | null;
  usage?: { inputTokens?: number | null; outputTokens?: number | null } | null;
};

export async function postAssistantMessage(
  technicianId: string,
  body: AssistantMessageBody,
): Promise<AssistantMessageResponse> {
  const url =
    `${API_BASE_URL}/assistant/messages?technicianId=${encodeURIComponent(technicianId)}`;
  const response = await fetch(url, {
    method: "POST",
    headers: await authHeaders(),
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`POST /assistant/messages failed with status ${response.status}`);
  }
  const data = (await response.json()) as AssistantMessageResponse;
  return data;
}
