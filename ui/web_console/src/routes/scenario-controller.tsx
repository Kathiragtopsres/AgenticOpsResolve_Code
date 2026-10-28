import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { Activity, CheckCheck, CircleHelp, Package, RotateCcw, Timer, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ops-shell";
import { formatDateTime, nowUtcTime } from "@/lib/utils";
import {
  scenarioReset,
  scenarioTelemetry,
  scenarioTechnicianDelay,
  scenarioPartShortage,
  scenarioCompleteWorkOrder,
  getTelemetryProfiles,
  type ScenarioResponse,
  type TelemetryProfile,
} from "@/services/service";
export const Route = createFileRoute("/scenario-controller")({
  head: () => ({
    meta: [
      { title: "Scenario Controller | OpsResolve" },
      { name: "description", content: "Run and reset service resolution demo scenarios." },
      { property: "og:title", content: "Scenario Controller | OpsResolve" },
      { property: "og:description", content: "Run and reset service resolution demo scenarios." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: Scenario,
});
function Scenario() {
  const [logs, setLogs] = useState<string[]>(() => [
    `[${nowUtcTime()}] INFO  Demo environment ready`,
    `[${nowUtcTime()}] INFO  Awaiting scenario command`,
  ]);
  const [auto, setAuto] = useState(true);
  // Console log container; auto-scrolled to the bottom on new logs when `auto` is on.
  const logRef = useRef<HTMLDivElement>(null);
  const [dialog, setDialog] = useState<"reset" | "guide" | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  // Telemetry profiles for the dropdown (GET /scenario/telemetry/profiles).
  const [profiles, setProfiles] = useState<TelemetryProfile[]>([]);
  const [profilesLoading, setProfilesLoading] = useState(true);
  const [scenario, setScenario] = useState("");
  useEffect(() => {
    let active = true;
    setProfilesLoading(true);
    getTelemetryProfiles()
      .then(({ profiles, defaultProfileId }) => {
        if (!active) return;
        setProfiles(profiles);
        setScenario(defaultProfileId || profiles[0]?.id || "");
      })
      .catch(() => {
        if (active) setProfiles([]);
      })
      .finally(() => {
        if (active) setProfilesLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  // Keep the console pinned to the latest line when auto-scroll is enabled.
  useEffect(() => {
    if (auto && logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logs, auto]);

  function appendLogs(res: ScenarioResponse) {
    const lines = res.logs.map(
      (l) => `[${formatDateTime(l.ts)}] ${l.level ?? "INFO"}  ${l.message ?? ""}`,
    );
    if (!lines.length) {
      lines.push(
        res.ok
          ? `[${nowUtcTime()}] INFO  Scenario triggered`
          : `[${nowUtcTime()}] ERROR  ${res.error?.message ?? "Scenario failed"}`,
      );
    }
    setLogs((prev) => [...prev, ...lines]);
  }

  // Runs a scenario action: blocks concurrent clicks, then pipes its logs
  // (or the error) into the console. `key` marks which button is busy.
  async function run(key: string, fn: () => Promise<ScenarioResponse>) {
    if (busy) return;
    setBusy(key);
    try {
      const res = await fn();
      appendLogs(res);
    } catch (e: any) {
      setLogs((prev) => [
        ...prev,
        `[${nowUtcTime()}] ERROR  ${e?.message ?? "Request failed"}`,
      ]);
    } finally {
      setBusy(null);
    }
  }

  const controls = [
    {
      key: "reset",
      icon: RotateCcw,
      title: "Reset Demo Data",
      description: "Clear cases, messages and approvals. Restore baseline state.",
      button: "Reset Now",
      action: () => setDialog("reset"),
    },
    {
      key: "telemetry",
      icon: Activity,
      title: "Start Telemetry Scenario",
      description: "Start a predefined telemetry scenario for the connected asset.",
      button: "Start Scenario",
      action: () =>
        run("telemetry", () => {
          const p = profiles.find((x) => x.id === scenario);
          return scenarioTelemetry(scenario, p?.assetId);
        }),
    },
    {
      key: "delay",
      icon: Timer,
      title: "Simulate Technician Delay",
      description: "Introduce a delay before the technician starts the work.",
      button: "Simulate Delay",
      action: () => run("delay", () => scenarioTechnicianDelay()),
    },
    {
      key: "shortage",
      icon: Package,
      title: "Simulate Part Shortage",
      description: "Simulate unavailability of the required part in inventory.",
      button: "Simulate Shortage",
      action: () => run("shortage", () => scenarioPartShortage()),
    },
    {
      key: "complete",
      icon: CheckCheck,
      title: "Force Complete Work Order",
      description: "Force completion of the current work order and close the case.",
      button: "Complete Now",
      action: () => run("complete", () => scenarioCompleteWorkOrder()),
    },
  ];

  async function doReset() {
    setDialog(null);
    await run("reset", () => scenarioReset());
  }

  return (
    <>
      <PageHeader
        title="Scenario Controller"
        description="Run demo scenarios, simulate events and control the end-to-end experience."
        actions={
          <Button variant="outline" onClick={() => setDialog("guide")}>
            <CircleHelp /> Scenario guide
          </Button>
        }
      />
      {/* Scenario control cards — reset + the demo event triggers */}
      <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-4">
        {controls.map(({ key, icon: Icon, title, description, button, action }) => (
          <div className="panel p-5 min-h-[205px] flex flex-col" key={title}>
            <div className="flex gap-3">
              <div className="p-2 rounded-md bg-accent text-primary h-fit">
                <Icon size={19} />
              </div>
              <div>
                <h2 className="text-sm font-bold text-sidebar">{title}</h2>
                <p className="text-xs leading-5 text-muted-foreground mt-1">{description}</p>
              </div>
            </div>
            {title === "Start Telemetry Scenario" && (
              <select
                className="field mt-3 w-full"
                aria-label="Choose scenario"
                value={scenario}
                onChange={(e) => setScenario(e.target.value)}
                disabled={profilesLoading}
              >
                {profilesLoading ? (
                  <option value="">Loading profiles...</option>
                ) : profiles.length ? (
                  profiles.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.label}
                    </option>
                  ))
                ) : (
                  <option value="">No profiles available</option>
                )}
              </select>
            )}
            <Button
              className="w-full mt-auto !h-9"
              onClick={action}
              disabled={!!busy || (key === "telemetry" && (profilesLoading || !scenario))}
            >
              {busy === key ? "Running..." : button}
            </Button>
          </div>
        ))}
      </div>
      {/* Console log — live output from triggered scenarios (auto-scrolls) */}
      <div className="mt-5 rounded-md bg-sidebar text-sidebar-foreground overflow-hidden">
        <div className="flex flex-wrap justify-between items-center gap-2 px-5 py-3 border-b border-sidebar-foreground/20">
          <h2 className="text-xs font-semibold">Scenario Console Log</h2>
          <div className="flex items-center gap-3 text-[11px]">
            <label className="flex gap-2 items-center">
              <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />{" "}
              Auto-scroll
            </label>
            <Button
              variant="ghost"
              size="sm"
              className="text-sidebar-foreground"
              onClick={() => setLogs([])}
            >
              <Trash2 size={13} /> Clear log
            </Button>
          </div>
        </div>
        <div
          ref={logRef}
          className="h-60 overflow-y-auto px-5 py-4 font-mono text-[11px] leading-6 text-sidebar-foreground/75"
        >
          {logs.map((line, i) => {
            const idx = line.indexOf("INFO") >= 0 ? line.indexOf("INFO") + 4 : line.indexOf("ERROR") + 5;
            return (
              <div key={i}>
                <span className="text-success">{line.slice(0, idx)}</span>
                {line.slice(idx)}
              </div>
            );
          })}
        </div>
      </div>
      {dialog && (
        <div className="dialog-backdrop" onClick={() => setDialog(null)}>
          <div className="dialog" onClick={(e) => e.stopPropagation()}>
            <div className="flex justify-between">
              <h2>{dialog === "reset" ? "Reset demo data?" : "Scenario guide"}</h2>
              <Button variant="ghost" size="icon" title="Close" onClick={() => setDialog(null)}>
                <X />
              </Button>
            </div>
            <p>
              {dialog === "reset"
                ? "This restores all sample cases, approvals, timelines and the console log to the original demo state."
                : "Start a telemetry scenario to create a case, then simulate a delay or part shortage. Review the changes in Case Board and Timeline, or complete the work order to close the primary case."}
            </p>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setDialog(null)}>
                Cancel
              </Button>
              {dialog === "reset" && (
                <Button onClick={doReset} disabled={!!busy}>
                  {busy === "reset" ? "Resetting..." : "Reset data"}
                </Button>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
