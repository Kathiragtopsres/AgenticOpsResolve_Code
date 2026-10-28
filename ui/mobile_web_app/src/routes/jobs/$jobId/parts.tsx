import { createFileRoute } from "@tanstack/react-router";
import { PartsScreen } from "@/components/field-screens";
export const Route = createFileRoute("/jobs/$jobId/parts")({
  head: () => ({
    meta: [
      { title: "Parts & Inventory | OpsResolve Field" },
      { name: "description", content: "Required parts and truck inventory for this job." },
      { property: "og:title", content: "Parts & Inventory | OpsResolve Field" },
      { property: "og:description", content: "Required parts and truck inventory for this job." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: () => {
    const { jobId } = Route.useParams();
    return <PartsScreen id={jobId} />;
  },
});
