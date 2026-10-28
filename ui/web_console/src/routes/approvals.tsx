import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { Check, ChevronLeft, ChevronRight, Clock3, Filter, ShieldCheck, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Empty, PageHeader, Status, Loader } from "@/components/ops-shell";
import { getApprovals, decideApproval, type ApprovalItem } from "@/services/service";
import { setPendingCount } from "@/lib/approvals-store";


export const Route = createFileRoute("/approvals")({
  head: () => ({
    meta: [
      { title: "Approvals Inbox | OpsResolve" },
      { name: "description", content: "Review human approval requests from service agents." },
      { property: "og:title", content: "Approvals Inbox | OpsResolve" },
      { property: "og:description", content: "Review human approval requests from service agents." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: Approvals,
});
function Approvals() {
  const [tab, setTab] = useState<"Pending" | "Decided">("Pending");
  const [priority, setPriority] = useState("All");
  // `choice` drives the confirm dialog: the row being acted on + intended outcome (null = closed).
  const [choice, setChoice] = useState<{
    approval: ApprovalItem;
    status: "APPROVED" | "REJECTED";
  } | null>(null);
  const [reason, setReason] = useState("");
  const [showFilters, setShowFilters] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [decideError, setDecideError] = useState("");
  // Cached per-tab lists. undefined = not yet fetched.
  const [pending, setPending] = useState<ApprovalItem[] | undefined>(undefined);
  const [decided, setDecided] = useState<ApprovalItem[] | undefined>(undefined);
  const [loading, setLoading] = useState(false);

  // silent = background refresh: never toggles the loader and keeps the
  // existing list on screen if the request fails.
  async function loadTab(which: "Pending" | "Decided", force = false, silent = false) {
    const hasData = which === "Pending" ? pending !== undefined : decided !== undefined;
    if (hasData && !force) return;
    if (!silent) setLoading(true);
    try {
      const data = await getApprovals(which === "Pending" ? "PENDING" : "DECIDED");
      if (which === "Pending") {
        setPending(data);
        setPendingCount(data.length); // keep the sidebar badge in sync
      } else {
        setDecided(data);
      }
    } catch {
      // Keep existing data visible on a silent refresh; clear only on a
      // visible (first) load.
      if (!silent) {
        if (which === "Pending") setPending([]);
        else setDecided([]);
      }
    } finally {
      if (!silent) setLoading(false);
    }
  }

  // Fetch the active tab lazily; cached tabs switch without refetching.
  useEffect(() => {
    loadTab(tab);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  // Auto-refresh the active tab every 20s (silent — keeps the list on screen).
  useEffect(() => {
    const id = setInterval(() => loadTab(tab, true, true), 20000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  const current = tab === "Pending" ? pending : decided;
  const pendingCount = pending?.length ?? 0;
  const decidedCount = decided?.length ?? 0;
  // Filter on the client so switching priority feels instant (no refetch).
  const rows = (current ?? []).filter((a) => priority === "All" || a.priority === priority);

  async function confirmDecision() {
    if (!choice) return;
    setSubmitting(true);
    setDecideError("");
    try {
      await decideApproval(
        choice.approval.id,
        choice.status === "APPROVED" ? "APPROVE" : "REJECT",
        reason,
      );
      setChoice(null);
      setReason("");
      // The approval moved from Pending to Decided: refetch both tabs.
      await Promise.all([loadTab("Pending", true), loadTab("Decided", true)]);
    } catch (e: any) {
      setDecideError(e?.message ?? "Could not record the decision.");
      // Refresh in case someone else already decided it.
      await loadTab("Pending", true);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Approvals Inbox"
        description="Review and decide on actions that require your approval."
        actions={
          <Button variant="outline" onClick={() => setShowFilters(!showFilters)}>
            <Filter /> Filters
          </Button>
        }
      />
      <div className="flex justify-between gap-3 items-center border-b mb-4">
        <div className="tab-row !border-0">
          <Button
            variant="ghost"
            className={`tab-button ${tab === "Pending" ? "selected" : ""}`}
            onClick={() => setTab("Pending")}
          >
            Pending{pendingCount > 0 ? ` (${pendingCount})` : ""}
          </Button>
          <Button
            variant="ghost"
            className={`tab-button ${tab === "Decided" ? "selected" : ""}`}
            onClick={() => setTab("Decided")}
          >
            Decided{decidedCount > 0 ? ` (${decidedCount})` : ""}
          </Button>
        </div>
        {showFilters && (
          <select
            className="field mb-2"
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
            aria-label="Filter priority"
          >
            <option>All</option>
            <option>P1</option>
            <option>P2</option>
            <option>P3</option>
            <option>P4</option>
          </select>
        )}
      </div>
      {/* Full loader only on first fetch; background refreshes keep the list on screen. */}
      {loading && current === undefined ? (
        <Loader />
      ) : (
        <>
          {/* Approval cards list — one card per pending/decided request */}
          <div className="space-y-2">
            {rows.length ? (
              rows.map((a) => (
                <div className="panel p-4 flex flex-col xl:flex-row xl:items-center gap-4" key={a.id}>
                  <div className="flex gap-3 items-start xl:w-[205px] shrink-0">
                    <div className="bg-accent text-primary rounded-md p-2.5">
                      <ShieldCheck size={19} />
                    </div>
                    <div className="min-w-0">
                      <Link
                        to="/cases/$caseId"
                        params={{ caseId: a.caseId }}
                        className="text-xs font-bold text-primary"
                      >
                        {a.caseId}
                      </Link>
                      <div className="text-[11px] text-muted-foreground mt-1">
                        {a.customer || "Service case"}
                      </div>
                      <div className="mt-2">
                        <Status state={a.priority} />
                      </div>
                    </div>
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-semibold text-sidebar">{a.title}</div>
                    <div className="text-xs text-muted-foreground mt-1">{a.context}</div>
                    <div className="text-[11px] text-muted-foreground mt-2">
                      Requested by: {a.agent}
                      {a.createdAt ? ` · ${a.createdAt}` : ""}
                    </div>
                    <Link
                      to="/cases/$caseId"
                      params={{ caseId: a.caseId }}
                      className="text-[11px] text-primary mt-1 inline-block"
                    >
                      View evidence →
                    </Link>
                    {a.decision && (
                      <div className="text-[11px] text-muted-foreground mt-2">{a.decision}</div>
                    )}
                  </div>
                  {/* Pending rows show Approve/Reject + SLA countdown; decided rows show a status badge. */}
                  <div className="flex items-center gap-2 flex-nowrap xl:justify-end xl:w-[310px] shrink-0">
                    {a.status === "PENDING" ? (
                      <>
                        <span className="status status-pending mr-auto whitespace-nowrap">
                          <Clock3 size={12} className="mr-1 shrink-0" />
                          {a.remaining} remaining
                        </span>
                        <Button
                          className="!h-9 shrink-0"
                          onClick={() => setChoice({ approval: a, status: "APPROVED" })}
                        >
                          Approve
                        </Button>
                        <Button
                          variant="outline"
                          className="!h-9 shrink-0"
                          onClick={() => setChoice({ approval: a, status: "REJECTED" })}
                        >
                          Reject
                        </Button>
                      </>
                    ) : (
                      <Status state={a.status} />
                    )}
                  </div>
                </div>
              ))
            ) : (
              <Empty
                title={
                  tab === "Pending"
                    ? "All caught up — no approvals waiting."
                    : "No decisions yet."
                }
              />
            )}
          </div>
          <div className="section-footer">
            <span>
              Showing {rows.length ? 1 : 0} to {rows.length} of {rows.length}
            </span>
            <div className="flex gap-1">
              <Button variant="outline" size="icon" disabled title="Previous">
                <ChevronLeft />
              </Button>
              <Button variant="outline" size="icon" disabled title="Next">
                <ChevronRight />
              </Button>
            </div>
          </div>
        </>
      )}
      {/* Confirm dialog — backdrop click closes, stopPropagation keeps clicks inside the card. */}
      {choice && (
        <div className="dialog-backdrop" onClick={() => setChoice(null)}>
          <div className="dialog" onClick={(e) => e.stopPropagation()}>
            <div className="flex justify-between items-start">
              <h2>{choice.status === "APPROVED" ? "Confirm approval" : "Confirm rejection"}</h2>
              <Button variant="ghost" size="icon" title="Close" onClick={() => setChoice(null)}>
                <X />
              </Button>
            </div>
            <p>
              {choice.status === "APPROVED" ? "You are approving" : "You are rejecting"}:{" "}
              <strong className="text-sidebar">{choice.approval.title}</strong>
            </p>
            <label className="eyebrow mb-2 block">Comments</label>
            <textarea
              className="field !h-24 !py-2 w-full"
              placeholder={
                choice.status === "APPROVED"
                  ? "Add a comment (optional)"
                  : "Reason for rejection (optional)"
              }
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              maxLength={1000}
            />
            {decideError && <p className="text-xs text-critical mt-2">{decideError}</p>}
            <div className="flex justify-end gap-2 mt-5">
              <Button variant="outline" onClick={() => setChoice(null)} disabled={submitting}>
                Cancel
              </Button>
              <Button onClick={confirmDecision} disabled={submitting}>
                <Check /> {submitting ? "Submitting..." : "Confirm"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
