import { createFileRoute } from "@tanstack/react-router";
import { DetailScreen } from "@/components/field-screens";
export const Route = createFileRoute("/jobs/$jobId/")({
  head: () => ({
    meta: [
      { title: "Job Detail | OpsResolve Field" },
      { name: "description", content: "Service request details and pre-job brief." },
      { property: "og:title", content: "Job Detail | OpsResolve Field" },
      { property: "og:description", content: "Service request details and pre-job brief." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: () => {
    const { jobId } = Route.useParams();
    return <DetailScreen id={jobId} />;
  },
});
