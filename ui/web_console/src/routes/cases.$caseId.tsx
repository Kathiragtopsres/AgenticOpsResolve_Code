import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  CalendarDays,
  CheckCircle2,
  ChevronDown,
  Download,
  FileText,
  Link2,
  Mail,
  MessageSquare,
  Paperclip,
  Phone,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
  UserRound,
  Wrench,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { PageHeader, Status, DownloadButton, Loader } from "@/components/ops-shell";
import { formatDateTime, nowUtc } from "@/lib/utils";
import {
  getCaseById,
  getCaseTimeline,
  type CaseDetailData,
  type TimelineEvent,
} from "@/services/service";
export const Route = createFileRoute("/cases/$caseId")({
  head: () => ({
    meta: [
      { title: "Case Detail | OpsResolve" },
      { name: "description", content: "Review a service case and its decision timeline." },
      { property: "og:title", content: "Case Detail | OpsResolve" },
      { property: "og:description", content: "Review a service case and its decision timeline." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: CaseDetail,
});
const tabs = [
  "Timeline",
  "Case Information",
  "Customer",
  "Asset & Entitlements",
  "Service Plan",
  "Related Items",
  "Messages",
  "Attachments",
];
const iconMap: Record<string, typeof TriangleAlert> = {
  alert: TriangleAlert,
  diagnosis: Sparkles,
  policy: ShieldCheck,
  message: MessageSquare,
  appointment: CalendarDays,
};

const val = (v: unknown): string => {
  if (v === null || v === undefined) return "";
  return String(v);
};
// Human-readable file size from a byte count (e.g. 48213 -> "47.1 KB").
const formatBytes = (bytes: unknown): string => {
  if (typeof bytes !== "number" || !isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let size = bytes / 1024;
  let i = 0;
  while (size >= 1024 && i < units.length - 1) {
    size /= 1024;
    i++;
  }
  return `${size.toFixed(1)} ${units[i]}`;
};
// Shared vertical timeline — reused by both Case Detail and the global Timeline page.
// `caseid` appends the case id to each row (only useful on the global feed).
export function TimelineList({
  events,
  loading,
  caseid
}: {
  events: TimelineEvent[];
  caseid?: boolean;
  loading?: boolean;
}) {
  const [open, setOpen] = useState<string[]>([]);
  const list = events;
  if (loading) return <Loader />;
  if (!list.length)
    return (
      <div className="empty">
        <h3>No timeline activity</h3>
        <p>Activity recorded for this case will appear here.</p>
      </div>
    );
  return (
    <div className="relative space-y-3">
      {list.map((e) => {
        const Icon = iconMap[e.type] || Sparkles;
        return (
          <div key={e.id} className="flex gap-4 md:gap-6">
            <div className="w-28 shrink-0 text-[11px] text-muted-foreground text-right pt-4 hidden sm:block">
              {formatDateTime(e.time)}
            </div>
            <div className="flex flex-col items-center">
              <div
                className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 ${e.type === "alert" ? "bg-destructive/10 text-critical" : e.type === "policy" ? "bg-success/10 text-success" : "bg-accent text-primary"}`}
              >
                <Icon size={15} />
              </div>
              <div className="w-px flex-1 bg-border min-h-8" />
            </div>
            <div className="panel flex-1 mb-1 min-w-0">
              <Button
                variant="ghost"
                className="!h-auto !whitespace-normal w-full !justify-between !items-start !p-4 text-left"
                onClick={() =>
                  setOpen(open.includes(e.id) ? open.filter((id) => id !== e.id) : [...open, e.id])
                }
              >
                <div className="min-w-0">
                  <div className="font-bold text-sidebar text-sm">
                    {e.title}{
                      caseid &&
                      <span>
                       {" "} - {e.caseId}
                      </span>
                    }{" "}
                    <Status
                      state={
                        e.type === "alert"
                          ? "High"
                          : e.type === "policy"
                            ? "Permit"
                            : e.type === "appointment"
                              ? "Scheduled"
                              : "Completed"
                      }
                    />
                  </div>
                  <div className="text-xs text-muted-foreground mt-2 leading-5 font-normal">
                    {e.description}
                    
                  </div>
                  <div className="text-[11px] text-muted-foreground mt-2 sm:hidden">
                    {formatDateTime(e.time)}
                  </div>
                </div>
                <ChevronDown size={16} className="mt-1" />
              </Button>
              {open.includes(e.id) && (
                <div className="border-t p-4 text-xs flex flex-wrap gap-2">
                  <span className="text-muted-foreground">Source: {e.source}</span>
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
function CaseDetail() {
  const { caseId } = Route.useParams();
  return <CaseDetailContent caseId={caseId} />;
}
function FieldGrid({ items }: { items: Array<[string, string]> }) {
  return (
    <div className="grid sm:grid-cols-2 gap-x-8 gap-y-5">
      {items.map(([label, value]) => (
        <div key={label} className="border-b pb-3 min-w-0">
          <div className="eyebrow mb-2">{label}</div>
          <div className="text-sm font-semibold text-sidebar break-words">{value}</div>
        </div>
      ))}
    </div>
  );
}

// Renders the panel for the active detail tab (Case Info / Customer / Asset / etc.).
// Falls through to the Attachments panel as the default.
function CaseTab({ tab, c }: { tab: string; c: CaseDetailData }) {
  if (tab === "Case Information")
    return (
      <div className="panel p-6 max-w-4xl">
        <div className="flex items-center gap-3 mb-6">
          <FileText className="text-primary" size={20} />
          <div>
            <h2 className="panel-heading">Case Information</h2>
            <p className="text-xs text-muted-foreground mt-1">Issue, ownership, and intake details</p>
          </div>
        </div>
        <FieldGrid
          items={[
            ["Issue summary", val(c.title)],
            ["Case ID", val(c.caseId)],
            ["Category", val(c.category)],
            ["Subcategory", val(c.subcategory)],
            ["Opened via", val(c.sourceChannel)],
            ["Opened", formatDateTime(c.createdAt)],
            ["Assigned queue", val(c.assignedQueue)],
            ["Case owner", val(c.caseOwner)],
          ]}
        />
        <div className="mt-6">
          <div className="eyebrow mb-2">Issue description</div>
          <p className="text-sm leading-6 text-foreground">{val(c.customerDescription)}</p>
        </div>
      </div>
    );
  if (tab === "Customer")
    return (
      <div className="panel p-6 max-w-4xl">
        <div className="flex items-center gap-3 mb-6">
          <UserRound className="text-primary" size={20} />
          <div>
            <h2 className="panel-heading">Customer</h2>
            <p className="text-xs text-muted-foreground mt-1">Account and primary contact</p>
          </div>
        </div>
        <FieldGrid
          items={[
            ["Account", val(c.customer?.name ?? c.customerName)],
            ["Account ID", val(c.customer?.customerId ?? c.customerId)],
            ["Segment", val(c.customer?.segment)],
            ["Primary contact", val(c.customer?.primaryContact?.name)],
            ["Role", val(c.customer?.primaryContact?.role)],
            ["Email", val(c.customer?.primaryContact?.email)],
            ["Phone", val(c.customer?.primaryContact?.phone)],
            ["Preferred channel", val(c.customer?.preferredChannel ?? c.channel)],
            ["Service location", val(c.customer?.serviceLocation?.name)],
            ["Address", val(c.customer?.serviceLocation?.address)],
          ]}
        />
        <div className="flex flex-wrap gap-2 mt-6">
          <Button variant="outline" size="sm">
            <Mail size={14} /> Email contact
          </Button>
          <Button variant="outline" size="sm">
            <Phone size={14} /> Call contact
          </Button>
        </div>
      </div>
    );
  if (tab === "Asset & Entitlements")
    return (
      <div className="grid lg:grid-cols-2 gap-5 max-w-5xl">
        <div className="panel p-6">
          <div className="flex items-center gap-3 mb-6">
            <Wrench className="text-primary" size={20} />
            <h2 className="panel-heading">Asset</h2>
          </div>
          <FieldGrid
            items={[
              ["Asset", val(c.asset?.displayName ?? c.asset?.model ?? c.assetId)],
              ["Asset ID", val(c.asset?.assetId ?? c.assetId)],
              ["Model", val(c.asset?.model)],
              ["Serial number", val(c.asset?.serialNumber)],
              ["Criticality", val(c.asset?.criticality)],
              ["Installed", formatDateTime(c.asset?.installedAt)],
              ["Site", `${val(c.asset?.site?.siteId)} - ${val(c.asset?.site?.name)}`],
              ["Last service", formatDateTime(c.asset?.lastServiceAt)],
            ]}
          />
        </div>
        <div className="panel p-6">
          <div className="flex items-center gap-3 mb-6">
            <ShieldCheck className="text-success" size={20} />
            <h2 className="panel-heading">Entitlements</h2>
          </div>
          <FieldGrid
            items={[
              ["Coverage", val(c.entitlement?.coverage ?? c.entitlement?.slaTier)],
              ["Contract", val(c.entitlement?.contractId)],
              ["Coverage status", val(c.entitlement?.coverageStatus)],
              [
                "Parts",
                c.entitlement?.partsCovered === null || c.entitlement?.partsCovered === undefined
                  ? ""
                  : c.entitlement.partsCovered
                    ? "Covered"
                    : "Not covered",
              ],
              [
                "Labor",
                c.entitlement?.laborCovered === null || c.entitlement?.laborCovered === undefined
                  ? ""
                  : c.entitlement.laborCovered
                    ? "Covered"
                    : "Not covered",
              ],
              ["Expires", formatDateTime(c.entitlement?.expiresAt)],
            ]}
          />
          <div className="mt-5 flex items-center gap-2 text-sm text-success font-semibold">
            <CheckCircle2 size={16} />{" "}
            {c.entitlement?.coverageStatus === "ACTIVE"
              ? "Service is entitled"
              : val(c.entitlement?.coverageStatus)}
          </div>
        </div>
      </div>
    );
  if (tab === "Service Plan") {
    const appt = c.appointments[0];
    return (
      <div className="panel p-6 max-w-5xl">
        <div className="flex items-center justify-between gap-4 mb-6">
          <div>
            <h2 className="panel-heading">Service Plan</h2>
            <p className="text-xs text-muted-foreground mt-1">Current resolution path and field visit</p>
          </div>
          <Status state={val(c.state) || "Open"} />
        </div>
        <div
          className="grid gap-5"
          style={{
            gridTemplateColumns: `repeat(${Math.min(Math.max(c.plans.length, 1), 3)}, minmax(0, 1fr))`,
          }}
        >
          {c.plans.length ? (
            c.plans.map((p, i) => {
              const goals = Array.isArray(p?.goals)
                ? p.goals.map((g: any) => val(g?.goal)).filter(Boolean).join(", ")
                : "";
              return (
                <div key={p?.version ?? i} className="border-l-2 border-primary pl-4 py-1">
                  <div className="eyebrow mb-2">
                    Step {i + 1} · {val(p?.version)}
                  </div>
                  <div className="font-bold text-sidebar text-sm">{goals}</div>
                  <p className="text-xs text-muted-foreground leading-5 mt-2">{val(p?.rationale)}</p>
                </div>
              );
            })
          ) : (
            <div className="text-sm text-muted-foreground">No service plan available.</div>
          )}
        </div>
        <div className="border-t mt-7 pt-5">
          <FieldGrid
            items={[
              ["Assigned technician", val(appt?.technicianName ?? appt?.technicianId)],
              ["Appointment", formatDateTime(appt?.slotStart)],
              [
                "Estimated duration",
                appt?.estimatedDurationMinutes != null
                  ? `${appt.estimatedDurationMinutes} min`
                  : "",
              ],
              [
                "Required part",
                c.requiredPart
                  ? `${val(c.requiredPart.name)}${c.requiredPart.qty ? ` ×${c.requiredPart.qty}` : ""}`
                  : "",
              ],
            ]}
          />
        </div>
      </div>
    );
  }
  if (tab === "Related Items") {
    const rows: Array<[string, string, string, string]> = [
      ...c.appointments.map(
        (a): [string, string, string, string] => [
          val(a?.workOrderId),
          "Work order",
          val(a?.technicianName ?? a?.technicianId),
          val(a?.status),
        ],
      ),
      ...c.approvals.map(
        (a): [string, string, string, string] => [
          val(a?.approvalId),
          "Approval",
          val(a?.action),
          val(a?.status),
        ],
      ),
    ];
    return (
      <div className="panel overflow-hidden max-w-5xl">
        <div className="p-6 border-b">
          <div className="flex items-center gap-3">
            <Link2 className="text-primary" size={20} />
            <div>
              <h2 className="panel-heading">Related Items</h2>
              <p className="text-xs text-muted-foreground mt-1">
                Cases, work orders, and approvals linked to this case
              </p>
            </div>
          </div>
        </div>
        <div className="divide-y">
          {rows.length ? (
            rows.map(([id, type, title, status], i) => (
              <div
                key={`${id}-${i}`}
                className="p-5 flex flex-col sm:flex-row sm:items-center gap-3 sm:gap-5"
              >
                <div className="w-28 text-xs font-bold text-primary">{id}</div>
                <div className="flex-1">
                  <div className="text-sm font-semibold text-sidebar">{title}</div>
                  <div className="text-xs text-muted-foreground mt-1">{type}</div>
                </div>
                <Status state={status || "Open"} />
              </div>
            ))
          ) : (
            <div className="p-5 text-sm text-muted-foreground"></div>
          )}
        </div>
      </div>
    );
  }
  if (tab === "Messages")
    return (
      <div className="panel max-w-4xl overflow-hidden">
        <div className="p-6 border-b">
          <div className="flex items-center gap-3">
            <MessageSquare className="text-primary" size={20} />
            <div>
              <h2 className="panel-heading">Messages</h2>
              <p className="text-xs text-muted-foreground mt-1">
                Customer and service-team conversation
              </p>
            </div>
          </div>
        </div>
        <div className="divide-y">
          {c.messages.length ? (
            c.messages.map((m, i) => {
              const name = m?.direction === "INBOUND" ? "Customer" : "OpsResolve";
              const role = val(m?.channel);
              const time = formatDateTime(m?.sentAt);
              const message = val(m?.body);
              return (
                <div key={m?.messageId ?? i} className="p-5 flex gap-4">
                  <div className="w-9 h-9 rounded-full bg-accent text-primary flex items-center justify-center shrink-0 text-xs font-bold">
                    {name.split(" ").map((v) => v[0]).join("").slice(0, 2)}
                  </div>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-baseline gap-x-2">
                      <span className="text-sm font-bold text-sidebar">{name}</span>
                      <span className="text-xs text-muted-foreground">
                        {role} · {time}
                      </span>
                    </div>
                    <p className="text-sm leading-6 mt-2">{message}</p>
                  </div>
                </div>
              );
            })
          ) : (
            <div className="p-5 text-sm text-muted-foreground"></div>
          )}
        </div>
      </div>
    );
  return (
    <div className="panel max-w-4xl overflow-hidden">
      <div className="p-6 border-b">
        <div className="flex items-center gap-3">
          <Paperclip className="text-primary" size={20} />
          <div>
            <h2 className="panel-heading">Attachments</h2>
            <p className="text-xs text-muted-foreground mt-1">
              Field evidence uploaded from the mobile app
            </p>
          </div>
        </div>
      </div>
      <div className="divide-y">
        {c.attachments.length ? (
          c.attachments.map((a, i) => {
            const isImage =
              String(a?.kind).toUpperCase() === "PHOTO" ||
              String(a?.contentType ?? "").startsWith("image/");
            return (
              <div key={a?.attachmentId ?? i} className="p-5 flex items-center gap-4">
                <div className="w-14 h-14 rounded-md bg-accent overflow-hidden shrink-0 flex items-center justify-center">
                  {isImage && a?.previewUrl ? (
                    <img
                      src={a.previewUrl}
                      alt={val(a?.fileName)}
                      className="w-full h-full object-cover"
                    />
                  ) : (
                    <FileText className="text-primary" size={20} />
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-semibold text-sidebar truncate">
                    {val(a?.fileName)}
                  </div>
                  <div className="text-xs text-muted-foreground mt-1">
                    {val(a?.kind)} · {formatBytes(a?.sizeBytes)}
                  </div>
                  <div className="text-[11px] text-muted-foreground mt-1">
                    {a?.uploadedBy ? `${a.uploadedBy} · ` : ""}
                    {formatDateTime(a?.uploadedAt)}
                  </div>
                </div>
                {a?.downloadUrl && (
                  <Button variant="outline" size="sm" asChild className="shrink-0">
                    <a href={a.downloadUrl} target="_blank" rel="noopener noreferrer">
                      <Download size={14} /> Download
                    </a>
                  </Button>
                )}
              </div>
            );
          })
        ) : (
          <div className="p-5 text-sm text-muted-foreground">No attachments yet</div>
        )}
      </div>
    </div>
  );
}

function CaseDetailContent({ caseId }: { caseId: string }) {
  const [c, setC] = useState<CaseDetailData | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [tab, setTab] = useState("Timeline");
  const [timeline, setTimeline] = useState<TimelineEvent[]>([]);
  const [timelineLoading, setTimelineLoading] = useState(true);
  const [timelineRefreshing, setTimelineRefreshing] = useState(false);
  const [timelineTick, setTimelineTick] = useState(0);
  // Tracks which case the timeline currently holds, so a case change is
  // treated as a first load (full loader) rather than a silent refresh.
  const loadedCaseRef = useRef<string | null>(null);
  // Timestamp shown in the Timeline footer; set to "now" whenever the tab opens.
  const [updated, setUpdated] = useState("");
  useEffect(() => {
    let active = true;
    setLoading(true);
    setNotFound(false);
    getCaseById(caseId)
      .then((data) => {
        if (!active) return;
        setC(data);
      })
      .catch(() => {
        if (!active) return;
        setNotFound(true);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [caseId]);
  // Timeline fetch — re-runs on case change (full loader) and on manual
  // refresh / 20s auto-refresh (silent, keeps existing events visible).
  useEffect(() => {
    let active = true;
    // A first load is when the timeline doesn't already hold this case.
    const firstLoad = loadedCaseRef.current !== caseId;
    if (firstLoad) setTimelineLoading(true);
    else setTimelineRefreshing(true);
    getCaseTimeline(caseId)
      .then((events) => {
        if (active) {
          setTimeline(events);
          loadedCaseRef.current = caseId;
        }
      })
      .catch(() => {
        // Keep existing events visible on a failed refresh; clear on first load.
        if (active && firstLoad) setTimeline([]);
      })
      .finally(() => {
        if (active) {
          setTimelineLoading(false);
          setTimelineRefreshing(false);
          setUpdated(nowUtc());
        }
      });
    return () => {
      active = false;
    };
  }, [caseId, timelineTick]);
  // Auto-refresh the timeline every 20s while the Timeline tab is open.
  useEffect(() => {
    if (tab !== "Timeline") return;
    const id = setInterval(() => setTimelineTick((t) => t + 1), 20000);
    return () => clearInterval(id);
  }, [tab]);
  if (loading) return <Loader />;
  if (notFound || !c)
    return (
      <div className="empty">
        <h3>Case not found</h3>
        <Link className="text-primary text-sm" to="/cases">
          Back to Case Board
        </Link>
      </div>
    );
  const details: Array<[string, string]> = [
    ["Case ID", val(c.caseId)],
    ["Customer", val(c.customerName)],
    ["Asset", val(c.assetId)],
    ["Opened", formatDateTime(c.createdAt)],
    ["Priority", val(c.priority)],
    ["SLA", val(c.sla)],
    ["State", val(c.state)],
    ["Channel", val(c.channel)],
  ];
  return (
    <>
      <Link to="/cases" className="text-xs text-primary flex items-center gap-1 mb-4">
        <ArrowLeft size={14} /> Back to Case Board
      </Link>
      <PageHeader
        title="Case Detail"
        description="End-to-end view of this service case across agents, systems and channels."
        actions={
          <>
            <DownloadButton data={{ case: c, events: timeline }} name={`${caseId}.json`}>
              <Download /> Export
            </DownloadButton>
            <Button asChild>
              <Link to="/trace" search={{ caseId }}>
                View trace
              </Link>
            </Button>
          </>
        }
      />
      {/* Case summary strip — key fields, stays pinned while scrolling */}
      <div className="panel grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-4 p-5 mb-5 sticky top-0 z-10">
        {details.slice(0, 7).map(([label, value]) => (
          <div key={label} className="min-w-0">
            <div className="eyebrow mb-2">{label}</div>
            {label === "Priority" || label === "State" ? (
              <Status state={value || "Open"} />
            ) : (
              <div
                className={`text-xs font-semibold text-sidebar break-words ${label === "Case ID" ? "text-primary" : ""}`}
              >
                {value}
              </div>
            )}
          </div>
        ))}
      </div>
      {/* Detail tabs — Timeline, Case Info, Customer, Asset, Messages, etc. */}
      <div className="tab-row mb-5">
        {tabs.map((t) => (
          <Button
            variant="ghost"
            key={t}
            className={`tab-button ${tab === t ? "selected" : ""}`}
            onClick={() => setTab(t)}
          >
            {t}
          </Button>
        ))}
      </div>
      {tab === "Timeline" ? (
        <>
          <TimelineList events={timeline} loading={timelineLoading} />
          <div className="section-footer">
            <span>Last updated: {updated}</span>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setTimelineTick((t) => t + 1)}
              disabled={timelineRefreshing}
            >
              <RefreshCw size={14} className={timelineRefreshing ? "animate-spin" : ""} /> Refresh
            </Button>
          </div>
        </>
      ) : (
        <CaseTab tab={tab} c={c} />
      )}
    </>
  );
}
