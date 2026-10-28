import { createFileRoute, Link, Outlet, useRouterState } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { Download, Settings2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { PageHeader, Status, Loader } from "@/components/ops-shell";
import { nowUtc } from "@/lib/utils";
import { getCasesSummary, getCases, type BoardCard } from "@/services/service";
export const Route = createFileRoute("/cases")({
  head: () => ({
    meta: [
      { title: "Case Board | OpsResolve" },
      { name: "description", content: "Monitor service cases by status and SLA urgency." },
      { property: "og:title", content: "Case Board | OpsResolve" },
      { property: "og:description", content: "Monitor service cases by status and SLA urgency." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: CaseBoard,
});
const tones: Record<CaseState, string> = {
  Open: "border-muted-foreground",
  Diagnosing: "border-info",
  Scheduled: "border-primary",
  "At Risk": "border-warning",
  "In Service": "border-secondary",
  Closed: "border-success",
};

type CaseState = 'Open' | 'Diagnosing' | 'Scheduled' | 'At Risk' | 'In Service' | 'Closed';

const states: CaseState[] = ['Open', 'Diagnosing', 'Scheduled', 'At Risk', 'In Service', 'Closed'];


function CaseBoard() {
  // "/cases" is a layout route: render the board at the index, otherwise let the
  // child route (e.g. /cases/$caseId) take over via <Outlet />.
  const path = useRouterState({ select: (s) => s.location.pathname });
  if (path !== "/cases") return <Outlet />;
  return <BoardContent />;
}
function BoardContent() {
  const [filter, setFilter] = useState<CaseState | null>(null);
  const [expanded, setExpanded] = useState<CaseState[]>([]);
  const [auto, setAuto] = useState(true);
  const [modal, setModal] = useState(false);
  const [tick, setTick] = useState(0);
  const [summary, setSummary] = useState<{ columns: Record<string, number>; total: number } | null>(
    null,
  );
  const [boardCards, setBoardCards] = useState<BoardCard[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  useEffect(() => {
    let active = true;
    // Only show the full-page loader on the first fetch (no data yet).
    const firstLoad = boardCards === null && summary === null;
    if (firstLoad) setLoading(true);
    else setRefreshing(true);
    Promise.allSettled([getCasesSummary(), getCases()]).then(([s, c]) => {
      if (!active) return;
      // Keep existing data visible if a refetch fails; only overwrite on success.
      if (s.status === "fulfilled") setSummary(s.value);
      if (c.status === "fulfilled") setBoardCards(c.value);
      setLoading(false);
      setRefreshing(false);
    });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick]);
  // Auto-refresh every 20s while the toggle is On.
  useEffect(() => {
    if (!auto) return;
    const id = setInterval(() => setTick((t) => t + 1), 20000);
    return () => clearInterval(id);
  }, [auto]);
  const boardItems = boardCards ?? [];

  const totals = states.map((s) => ({
    state: s,
    count: summary ? (summary.columns[s] ?? "") : "",
  }));
  const totalCount = summary ? (typeof summary.total === "number" ? summary.total : "") : "";
  // Export the current board to a CSV file (header row + one row per case).
  function exportBoard() {
    const lines = [
      "Case ID,Customer,Issue,State,Priority,SLA",
      ...boardItems.map((c) =>
        [c.id, c.customer, c.issue, c.column, c.priority, c.sla].map((v) => `"${v}"`).join(","),
      ),
    ];
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
    a.download = "opsresolve-case-board.csv";
    a.click();
    URL.revokeObjectURL(a.href);
  }
  if (loading) return <Loader />;
  return (
    <>
      <div className="flex flex-col h-[calc(100vh-104px)]">
        <PageHeader
          title="Case Board"
          description="Real-time view of service cases across the lifecycle"
          actions={
            <>
              <Button variant="outline" onClick={() => setModal(true)}>
                <Settings2 /> Board settings
              </Button>
            </>
          }
        />
        {/* Status summary cards — one count per lifecycle state + total */}
        <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-2 mb-5 shrink-0">
          {totals.map(({ state, count }) => (
            <Button
              key={state}
              variant="outline"
              onClick={() => setFilter(filter === state ? null : state)}
              className={`!h-[66px] !justify-start !px-3 !flex-col !items-start !gap-1 !rounded-md ${filter === state ? "!border-primary !bg-accent" : ""}`}
            >
              <div className="flex items-center gap-2 w-full">
                <span className={`h-2 w-2 rounded-full border-4 ${tones[state]}`} />
                <span className="text-[11px] text-muted-foreground">{state}</span>
              </div>
              <strong className="text-lg text-sidebar">{count}</strong>
            </Button>
          ))}
          <div className="flex flex-col justify-center items-start pl-3">
            <span className="text-[11px] text-muted-foreground">Total cases</span>
            <strong className="text-xl text-sidebar">{totalCount}</strong>
          </div>
        </div>
        {/* Kanban board — one column per state, cards sorted by SLA urgency */}
        <div className="flex-1 min-h-0 overflow-auto pb-3">
          <div
            className={`grid gap-3 items-start min-h-full ${filter ? "grid-cols-1 max-w-[500px]" : "grid-cols-6 min-w-[1100px]"}`}
          >
            {states
              .filter((s) => !filter || s === filter)
              .map((state) => {
                const items = boardItems
                  .filter((c) => c.column === state)
                  .sort((a, b) => a.sla.localeCompare(b.sla));
                return (
                  <section key={state} className="min-w-0 flex flex-col h-full">
                    <div className="sticky top-0 z-10 bg-canvas pb-2">
                      <div
                        className={`flex items-center justify-between border-t-[3px] ${tones[state]} bg-background rounded-t-md border-x border-b px-3 h-11`}
                      >
                        <h2 className="text-xs font-bold text-sidebar">{state}</h2>
                        <span className="status">
                          {totals.find((t) => t.state === state)?.count}
                        </span>
                      </div>
                    </div>
                    <div className="space-y-2">
                      {/* Show 3 cards per column by default; "+ N more" expands the rest. */}
                      {items.length ? (
                        items.slice(0, expanded.includes(state) ? undefined : 3).map((c) => (
                          <Link
                            key={c.id}
                            to="/cases/$caseId"
                            params={{ caseId: c.id }}
                            className={`block panel p-3 no-underline text-foreground hover:border-primary transition-colors ${state === "At Risk" ? "border-l-4 !border-l-warning" : ""}`}
                          >
                            <div className="flex justify-between gap-1">
                              <span className="text-[11px] font-bold text-primary">{c.id}</span>
                              <span className="text-[10px] text-muted-foreground">◉</span>
                            </div>
                            <div
                              className="text-xs font-bold text-sidebar mt-2 truncate"
                              title={c.customer}
                            >
                              {c.customer}
                            </div>
                            <div className="text-[11px] text-muted-foreground mt-1 min-h-8 leading-4">
                              {c.issue}
                            </div>
                            <div className="flex items-center justify-between gap-1 mt-3">
                              <Status state={c.state} />
                              <Status state={c.priority} />
                            </div>
                            <div className="text-[10px] text-muted-foreground mt-2 flex gap-1">
                              {state === "Closed" ? "Resolved" : "SLA " + c.sla} 
                              {
                                c.channel&& <div>
                                  · {c.channel}
                                </div>
                              }
                            </div>
                          </Link>
                        ))
                      ) : (
                        <div className="text-xs text-muted-foreground p-5 text-center">
                          No cases in this state
                        </div>
                      )}
                      {items.length > 3 && !expanded.includes(state) && (
                        <Button
                          variant="ghost"
                          className="w-full text-xs text-primary"
                          onClick={() => setExpanded([...expanded, state])}
                        >
                          + {items.length - 3} more cases
                        </Button>
                      )}
                    </div>
                  </section>
                );
              })}
          </div>
        </div>
        {/* Board footer — last-updated stamp, auto-refresh toggle and export */}
        <div className="section-footer shrink-0 !mt-0">
          <span>Last updated: {nowUtc()}</span>
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />{" "}
              Auto-refresh: {auto ? "On" : "Off"}
            </label>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setTick(tick + 1)}
              title="Refresh"
              disabled={refreshing}
            >
              <RefreshCw size={14} className={refreshing ? "animate-spin" : ""} />
            </Button>
            <Button variant="ghost" size="sm" onClick={exportBoard}>
              <Download size={14} /> Export board
            </Button>
          </div>
        </div>
      </div>
      {/* Board settings dialog — focus the board on a single state */}
      {modal && (
        <div className="dialog-backdrop" onClick={() => setModal(false)}>
          <div className="dialog" onClick={(e) => e.stopPropagation()}>
            <h2>Board settings</h2>
            <p>Choose a state to focus the board. All case cards remain ordered by SLA urgency.</p>
            <select
              className="field w-full"
              value={filter || ""}
              onChange={(e) => setFilter((e.target.value as CaseState) || null)}
            >
              <option value="">All states</option>
              {states.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
            <div className="flex justify-end mt-5">
              <Button onClick={() => setModal(false)}>Done</Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
