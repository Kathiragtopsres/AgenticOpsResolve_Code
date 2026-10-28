import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { Clock3, RefreshCw, ShieldCheck, Star, Wrench } from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Button } from "@/components/ui/button";
import { PageHeader, Status, Loader } from "@/components/ops-shell";
import { formatDateTime, nowUtc } from "@/lib/utils";
import { getKpis, getRecentCases, type KpiData, type RecentCase } from "@/services/service";
export const Route = createFileRoute("/kpis")({
  head: () => ({
    meta: [
      { title: "KPI Dashboard | OpsResolve" },
      {
        name: "description",
        content: "Service resolution performance and customer experience metrics.",
      },
      { property: "og:title", content: "KPI Dashboard | OpsResolve" },
      {
        property: "og:description",
        content: "Service resolution performance and customer experience metrics.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: KPIs,
});
const chartColors = [
  "var(--color-primary)",
  "var(--color-success)",
  "var(--color-warning)",
  "var(--color-secondary)",
];
// The three fixed dropdown labels map to a weeks value for GET /kpis?weeks=.
const periodWeeks: Record<string, number> = {
  "Last 8 weeks": 8,
  "Last 4 weeks": 4,
  "This week": 1,
};
const channelOrder = ["RCS", "WhatsApp", "SMS", "Email"];
// casesByChannel keys from the API are upper-case; match them to the labels.
const channelApiKey: Record<string, string> = {
  RCS: "RCS",
  WhatsApp: "WHATSAPP",
  SMS: "SMS",
  Email: "EMAIL",
};
function KPIs() {
  const [period, setPeriod] = useState("Last 8 weeks");
  const [updated, setUpdated] = useState(() => nowUtc());
  const [kpis, setKpis] = useState<KpiData | null>(null);
  const [recent, setRecent] = useState<RecentCase[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let active = true;
    // Only show the full-page loader on the first fetch (no data yet).
    const firstLoad = kpis === null;
    if (firstLoad) setLoading(true);
    else setRefreshing(true);
    const weeks = periodWeeks[period] ?? 8;
    Promise.allSettled([getKpis(weeks), getRecentCases(5)]).then(([k, r]) => {
      if (!active) return;
      // Keep existing data visible if a refetch fails; only overwrite on success.
      if (k.status === "fulfilled") setKpis(k.value);
      if (r.status === "fulfilled") setRecent(r.value);
      setLoading(false);
      setRefreshing(false);
      setUpdated(nowUtc());
    });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [period, tick]);
  // Auto-refresh every 20s (the footer advertises periodic refresh).
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 20000);
    return () => clearInterval(id);
  }, []);

  if (loading) return <Loader />;

  // Metric values from the API (null -> "NA").
  const mttrHours =
    kpis && kpis.mttr.minutes !== null ? `${(kpis.mttr.minutes / 60).toFixed(1)}h` : "NA";
  const slaAvoided =
    kpis && typeof kpis.slaBreachesAvoided.total === "number"
      ? String(kpis.slaBreachesAvoided.total)
      : "NA";
  const remoteRate =
    kpis && kpis.remoteResolutionRate.rate !== null
      ? `${Math.round(kpis.remoteResolutionRate.rate * 100)}%`
      : "NA";
  const csat = kpis && kpis.csat.average !== null ? String(kpis.csat.average) : "NA";

  // Resolution time trend (mttrMinutes -> hours; null week plots as 0).
  const series = (kpis?.mttr.trend ?? []).map((t: any, i: number) => ({
    week: `W${i + 1}`,
    weekStart: formatDateTime(t?.weekStart),
    hours: typeof t?.mttrMinutes === "number" ? Number((t.mttrMinutes / 60).toFixed(1)) : 0,
    casesClosed: typeof t?.casesClosed === "number" ? t.casesClosed : 0,
  }));

  // Cases by channel pie, preserving the four fixed slices.
  const mix = channelOrder.map((name) => ({
    name,
    value: kpis?.casesByChannel?.[channelApiKey[name] ?? name] ?? 0,
  }));
  const total = mix.reduce((a, b) => a + b.value, 0);

  const metrics = [
    ["Mean Time to Resolution", mttrHours, Clock3],
    ["SLA Breaches Avoided", slaAvoided, ShieldCheck],
    ["Remote Resolution Rate", remoteRate, Wrench],
    ["Customer CSAT", csat, Star],
  ] as const;

  return (
    <>
      <PageHeader
        title="KPI Dashboard"
        description="Real-time overview of service operations performance"
        actions={
          <>
            <select
              className="field"
              value={period}
              onChange={(e) => setPeriod(e.target.value)}
              aria-label="Date range"
            >
              {Object.keys(periodWeeks).map((p) => (
                <option key={p}>{p}</option>
              ))}
            </select>
            <Button variant="outline" onClick={() => setPeriod("Last 8 weeks")}>
              Reset filters
            </Button>
          </>
        }
      />
      {/* Top KPI cards — MTTR, SLA breaches avoided, remote resolution, CSAT */}
      <div className="grid sm:grid-cols-2 xl:grid-cols-4 gap-3 mb-4">
        {metrics.map(([label, value, Icon]) => (
          <div key={label} className="panel p-5 min-h-[145px]">
            <div className="flex justify-between items-start">
              <span className="text-xs font-semibold text-sidebar">{label}</span>
              <span className="p-2 rounded-md bg-accent text-primary">
                <Icon size={18} />
              </span>
            </div>
            <div className="text-[32px] font-bold text-sidebar mt-3 leading-none">{value}</div>            
          </div>
        ))}
      </div>
      <div className="grid lg:grid-cols-[1.35fr_1fr] gap-4 mb-4">
        {/* Resolution time trend — area chart of weekly MTTR in hours */}
        <div className="panel p-5">
          <h2 className="panel-heading">Resolution Time Trend</h2>
          <p className="text-[11px] text-muted-foreground mb-5">Mean resolution time in hours</p>
          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={series} margin={{ left: 0, right: 10, bottom: 16 }}>
                <defs>
                  <linearGradient id="trendFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--color-primary)" stopOpacity={0.22} />
                    <stop offset="100%" stopColor="var(--color-primary)" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid vertical={false} stroke="var(--color-border)" />
                <XAxis
                  dataKey="week"
                  tick={{ fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  label={{
                    value: "Weeks",
                    position: "insideBottom",
                    offset: -8,
                    fontSize: 11,
                    fill: "var(--color-muted-foreground)",
                  }}
                />
                <YAxis
                  tick={{ fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  label={{
                    value: "Hours",
                    angle: -90,
                    position: "insideLeft",
                    fontSize: 11,
                    fill: "var(--color-muted-foreground)",
                  }}
                />
                <Tooltip
                  allowEscapeViewBox={{ x: true, y: true }}
                  wrapperStyle={{ zIndex: 50 }}
                  formatter={((value: any, _name: any, item: any) => {
                    const closed = item?.payload?.casesClosed ?? 0;
                    return [`${value} hours · ${closed} cases closed`, "Resolution time"];
                  }) as any}
                  labelFormatter={((label: any, payload: any) => {
                    const ws = payload?.[0]?.payload?.weekStart;
                    return ws ? (
                      <span>
                        {label}
                        <br />
                        starting {ws}
                      </span>
                    ) : (
                      String(label)
                    );
                  }) as any}
                />
                <Area
                  type="monotone"
                  dataKey="hours"
                  stroke="var(--color-primary)"
                  strokeWidth={2.5}
                  fill="url(#trendFill)"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>
        {/* Cases by channel — donut chart with total in the center + legend */}
        <div className="panel p-5">
          <h2 className="panel-heading">Cases by Channel</h2>
          <p className="text-[11px] text-muted-foreground mb-2">Customer engagement breakdown</p>
          <div className="flex flex-col sm:flex-row lg:flex-col xl:flex-row items-center">
            <div className="h-60 w-60 shrink-0 relative">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={mix} dataKey="value" innerRadius={65} outerRadius={88} paddingAngle={2}>
                    {mix.map((m, i) => (
                      <Cell key={m.name} fill={chartColors[i]} />
                    ))}
                  </Pie>
                  <Tooltip
                    allowEscapeViewBox={{ x: true, y: true }}
                    wrapperStyle={{ zIndex: 50 }}
                  />
                </PieChart>
              </ResponsiveContainer>
              <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                <strong className="text-2xl text-sidebar">{total}</strong>
                <span className="text-[11px] text-muted-foreground">Total cases</span>
              </div>
            </div>
            <div className="flex-1 w-full space-y-3">
              {mix.map((m, i) => (
                <div className="flex justify-between text-xs gap-3" key={m.name}>
                  <span className="flex gap-2 items-center">
                    <i
                      className="w-2.5 h-2.5 rounded-sm"
                      style={{ backgroundColor: chartColors[i] }}
                    />
                    {m.name}
                  </span>
                  <span className="text-muted-foreground">
                    {m.value} ({total ? Math.round((m.value / total) * 100) : 0}%)
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
      {/* Recent cases table — latest 5 cases with status/channel/SLA */}
      <div className="panel">
        <div className="p-5 flex justify-between items-center">
          <h2 className="panel-heading">Recent Cases</h2>
          <Button variant="outline" size="sm" onClick={() => (location.href = "/cases")}>
            View all cases →
          </Button>
        </div>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Case ID</th>
                <th>Customer</th>
                <th>Status</th>
                <th>Channel</th>
                <th>SLA</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {recent.map((c) => (
                <tr key={c.id}>
                  <td>
                    <Link
                      to="/cases/$caseId"
                      params={{ caseId: c.id }}
                      className="text-primary font-bold"
                    >
                      {c.id}
                    </Link>
                  </td>
                  <td>{c.customer}</td>
                  <td>
                    <Status state={c.state} />
                  </td>
                  <td>{c.channel}</td>
                  <td>{c.sla}</td>
                  <td>
                    <Link to="/cases/$caseId" params={{ caseId: c.id }} className="text-primary">
                      →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="section-footer">
        <span> </span>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setTick((t) => t + 1)}
          disabled={refreshing}
        >
          Last updated: {updated}{" "}
          <RefreshCw size={14} className={refreshing ? "animate-spin" : ""} />
        </Button>
      </div>
    </>
  );
}
