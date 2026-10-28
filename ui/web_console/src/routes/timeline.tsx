import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { PageHeader } from "@/components/ops-shell";
import { TimelineList } from "./cases.$caseId";
import { getTimeline, type TimelineEvent } from "@/services/service";
export const Route = createFileRoute("/timeline")({
  head: () => ({
    meta: [
      { title: "Activity Timeline | OpsResolve" },
      { name: "description", content: "Activity across service cases." },
      { property: "og:title", content: "Activity Timeline | OpsResolve" },
      { property: "og:description", content: "Activity across service cases." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: Timeline,
});
function Timeline() {
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let active = true;
    // Show the loader only on the first load; silent background refresh after.
    if (tick === 0) setLoading(true);
    getTimeline()
      .then((data) => {
        if (active) setEvents(data);
      })
      .catch(() => {
        // Keep existing events visible on a failed refresh; clear on first load.
        if (active && tick === 0) setEvents([]);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [tick]);
  // Auto-refresh every 20s while the page is open.
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 20000);
    return () => clearInterval(id);
  }, []);
  return (
    <>
      <PageHeader title="Timeline" description="Recent activity across all service cases" />
      <TimelineList events={events} loading={loading} caseid={true} />
    </>
  );
}
