import { createFileRoute, Link, useSearch } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Download, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DownloadButton, PageHeader, Status, Loader } from "@/components/ops-shell";
import { formatDateTime } from "@/lib/utils";
import {
  getCaseNames,
  getCaseTrace,
  type TraceSpan,
  type TraceData,
  type CaseName,
} from "@/services/service";
export const Route = createFileRoute("/trace")({
  validateSearch: (search: Record<string, unknown>): { caseId?: string | undefined } =>
    typeof search["caseId"] === "string" ? { caseId: search["caseId"] } : {},
  head: () => ({
    meta: [
      { title: "Trace Viewer | OpsResolve" },
      { name: "description", content: "Inspect agent decisions, tool calls and policy traces." },
      { property: "og:title", content: "Trace Viewer | OpsResolve" },
      { property: "og:description", content: "Inspect agent decisions, tool calls and policy traces." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: Trace,
});

// The span tree + detail panel. Receives fetched spans; purely presentational.
export function TraceContent({
  spans,
  trace,
  caseName,
}: {
  spans: TraceSpan[];
  trace: TraceData | null;
  caseName?: CaseName | undefined;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<string[]>([]);
  const [tab, setTab] = useState("Input");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("All");
  const current = spans.find((s) => s.id === selected) || spans[0];
  if (!current) return <div className="empty">No trace spans available.</div>;

  // A span is shown only if it passes the search + status filters AND none of
  // its ancestors are collapsed (so collapsing a parent hides its whole subtree).
  function visible(span: TraceSpan) {
    if (query && !`${span.name} ${span.description}`.toLowerCase().includes(query.toLowerCase()))
      return false;
    if (status !== "All" && span.status !== status) return false;
    let parent = span.parent;
    while (parent) {
      if (collapsed.includes(parent)) return false;
      parent = spans.find((s) => s.id === parent)?.parent;
    }
    return true;
  }

  const root = spans[0];
  return (
    <>
      {/* Trace summary strip — id, start time, duration, turn count, status */}
      <div className="panel p-4 grid grid-cols-2 md:grid-cols-5 gap-4 mb-4">
        {[
          ["Trace ID", trace?.caseId ?? "NA"],
          ["Start time", trace?.startedAt && trace.startedAt !== "" ? formatDateTime(trace.startedAt) : ""],
          ["Duration", root?.duration ?? ""],
          ["Turns", String(trace?.spanCount ?? spans.length)],
          ["Status", root?.status === "error" ? "Error" : "Success"],
        ].map(([l, v]) => (
          <div key={l}>
            <div className="eyebrow mb-2">{l}</div>
            <div className="text-xs font-semibold text-sidebar">
              {l === "Status" ? <Status state={v ?? "Success"} /> : v}
            </div>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-2 mb-4">
        <div className="relative flex-1 min-w-[180px] max-w-xs">
          <Search size={15} className="absolute left-3 top-3 text-muted-foreground" />
          <input
            className="field w-full !pl-9"
            placeholder="Search spans by name or type..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <Button variant="outline" onClick={() => setCollapsed([])}>
          Expand all
        </Button>
        <Button
          variant="outline"
          onClick={() =>
            setCollapsed(spans.filter((s) => spans.some((t) => t.parent === s.id)).map((s) => s.id))
          }
        >
          Collapse all
        </Button>
        <select
          className="field"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          aria-label="Filter trace status"
        >
          <option>All</option>
          <option>success</option>
          <option>error</option>
          <option>denied</option>
        </select>
        <div className="ml-auto">
          <DownloadButton data={spans} name="opsresolve-trace.json">
            <Download /> Download JSON
          </DownloadButton>
        </div>
      </div>
      <div className="grid lg:grid-cols-[minmax(350px,1.1fr)_minmax(320px,1fr)] gap-4">
        {/* Span tree — collapsible, indented list of agent turns/tool calls */}
        <div className="panel overflow-hidden">
          <div className="flex justify-between p-4 border-b">
            <h2 className="panel-heading">Span tree</h2>
            <span className="text-xs text-muted-foreground">{spans.length} spans</span>
          </div>
          <div>
            {spans.filter(visible).map((s) => {
              const depth = s.parent ? (spans.find((t) => t.id === s.parent)?.parent ? 2 : 1) : 0;
              const children = spans.some((t) => t.parent === s.id);
              return (
                <div
                  key={s.id}
                  className={`flex items-center gap-1 py-2 pr-3 border-b last:border-b-0 ${selected === s.id ? "bg-accent" : ""}`}
                  style={{ paddingLeft: `${12 + depth * 20}px` }}
                >
                  <Button
                    variant="ghost"
                    size="icon"
                    className="!h-7 !w-7"
                    title={children ? "Toggle children" : "No children"}
                    onClick={() =>
                      children &&
                      setCollapsed(
                        collapsed.includes(s.id)
                          ? collapsed.filter((id) => id !== s.id)
                          : [...collapsed, s.id],
                      )
                    }
                  >
                    {children ? (
                      collapsed.includes(s.id) ? (
                        <ChevronRight size={13} />
                      ) : (
                        <ChevronDown size={13} />
                      )
                    ) : (
                      <span className="w-3" />
                    )}
                  </Button>
                  <Button
                    variant="ghost"
                    className="min-w-0 !h-auto !flex-1 !justify-start !whitespace-normal !text-left !py-1"
                    onClick={() => {
                      setSelected(s.id);
                      setTab("Input");
                    }}
                  >
                    <div className="min-w-0">
                      <div className="text-xs font-semibold text-sidebar truncate">{s.name}</div>
                      <div className="text-[10px] text-muted-foreground truncate">
                        {s.description}
                      </div>
                    </div>
                  </Button>
                  <span className="text-[10px] text-muted-foreground shrink-0">{s.duration}</span>
                  <Status state={s.status} />
                </div>
              );
            })}
          </div>
          {caseName && (
            <div className="p-4 border-t bg-muted/30 text-xs">
              <div className="eyebrow mb-2">Case context</div>
              <div className="font-bold text-sidebar">
                {caseName.id} · {caseName.customer}
              </div>
              <div className="text-muted-foreground mt-1">{caseName.asset}</div>
              <Link
                to="/cases/$caseId"
                params={{ caseId: caseName.id }}
                className="text-primary inline-block mt-3"
              >
                View case details →
              </Link>
            </div>
          )}
        </div>
        {/* Span detail — Input/Output/Events/Annotations for the selected span */}
        <div className="panel overflow-hidden">
          <div className="p-4 border-b">
            <div className="eyebrow mb-2">Selected span · {current.type}</div>
            <h2 className="panel-heading">{current.name}</h2>
            <div className="flex gap-2 mt-2">
              <Status state={current.status} />
            </div>
          </div>
          <div className="tab-row">
            {["Input", "Output", "Events", "Annotations"].map((t) => (
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
          <pre className="p-5 text-[11px] leading-6 overflow-x-auto min-h-[380px] text-foreground">
            {(() => {
              const p =
                current.payload && typeof current.payload === "object"
                  ? (current.payload as { input?: unknown; output?: unknown })
                  : { input: null, output: null };
              return JSON.stringify(
                tab === "Input"
                  ? { span: current.name, input: p.input ?? null }
                  : tab === "Output"
                    ? { status: current.status, output: p.output ?? null }
                    : tab === "Events"
                      ? {
                          startedAt: current.startedAt,
                          duration: current.duration,
                          markers: current.markers,
                        }
                      : {
                          type: current.type,
                          status: current.status,
                          policyDecision: current.policyDecision,
                        },
                null,
                2,
              );
            })()}
          </pre>
        </div>
      </div>
      <div className="section-footer">
        <span>{spans.length} spans</span>
      </div>
    </>
  );
}

function Trace() {
  const { caseId: initialCaseId } = useSearch({ from: "/trace" });
  const [caseNames, setCaseNames] = useState<CaseName[]>([]);
  const [namesLoading, setNamesLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string>(initialCaseId ?? "");
  const [trace, setTrace] = useState<TraceData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  // Tracks which case the trace currently holds, so a case change is treated
  // as a first load (full loader) rather than a silent auto-refresh.
  const loadedCaseRef = useRef<string | null>(null);

  // Fetch the case list for the dropdown (independent of the top-bar search).
  useEffect(() => {
    let active = true;
    setNamesLoading(true);
    getCaseNames()
      .then((names) => {
        if (active) setCaseNames(names);
      })
      .catch(() => {
        if (active) setCaseNames([]);
      })
      .finally(() => {
        if (active) setNamesLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  // Pre-select the case passed from Case Detail ("View trace").
  useEffect(() => {
    if (initialCaseId) setSelectedId(initialCaseId);
  }, [initialCaseId]);

  useEffect(() => {
    if (!selectedId) {
      setTrace(null);
      loadedCaseRef.current = null;
      return;
    }
    let active = true;
    // Full loader only on first load for this case (trace not loaded yet).
    // Re-selects and the 20s auto-refresh keep the current trace visible.
    const firstLoad = loadedCaseRef.current !== selectedId;
    if (firstLoad) {
      setLoading(true);
      setError("");
    }
    getCaseTrace(selectedId)
      .then((data) => {
        if (active) {
          setTrace(data);
          setError("");
          loadedCaseRef.current = selectedId;
        }
      })
      .catch((e: any) => {
        if (active && firstLoad) {
          // Only surface the error screen on first load; a failed silent
          // refresh keeps the existing trace on screen.
          setTrace(null);
          setError(e?.message ?? "Could not load trace.");
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, tick]);

  // Auto-refresh the selected case's trace every 20s (silent).
  useEffect(() => {
    if (!selectedId) return;
    const id = setInterval(() => setTick((t) => t + 1), 20000);
    return () => clearInterval(id);
  }, [selectedId]);

  const caseName = caseNames.find((c) => c.id === selectedId);

  return (
    <>
      <PageHeader
        title="Trace Viewer"
        description="End-to-end view of agent turns, tool calls, responses and events"
      />
      <div className="mb-5 max-w-md">
        <label className="eyebrow mb-2 block">Case</label>
        <select
          className="field w-full"
          value={selectedId}
          onChange={(e) => setSelectedId(e.target.value)}
          aria-label="Select a case"
          disabled={namesLoading}
        >
          <option value="">{namesLoading ? "Loading cases..." : "Select a case"}</option>
          {caseNames.map((c) => (
            <option key={c.id} value={c.id}>
              {c.id} · {c.customer} · {c.issue}
            </option>
          ))}
        </select>
      </div>
      {!selectedId ? (
        <div className="empty">
          <h3>Select a case</h3>
          <p>Choose a case above to view its agent trace.</p>
        </div>
      ) : loading ? (
        <Loader />
      ) : error ? (
        <div className="empty">
          <h3>Could not load trace</h3>
          <p>{error}</p>
        </div>
      ) : (
        <TraceContent spans={trace?.spans ?? []} trace={trace} caseName={caseName} />
      )}
    </>
  );
}
