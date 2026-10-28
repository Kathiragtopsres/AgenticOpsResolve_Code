import { createFileRoute } from "@tanstack/react-router";
import { CompletionScreen } from "@/components/field-screens";
export const Route = createFileRoute("/jobs/$jobId/complete")({
  head: () => ({
    meta: [
      { title: "Job Completion | OpsResolve Field" },
      { name: "description", content: "Verification, photo evidence and customer sign-off." },
      { property: "og:title", content: "Job Completion | OpsResolve Field" },
      {
        property: "og:description",
        content: "Verification, photo evidence and customer sign-off.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: () => {
    const { jobId } = Route.useParams();
    return <CompletionScreen id={jobId} />;
  },
});
