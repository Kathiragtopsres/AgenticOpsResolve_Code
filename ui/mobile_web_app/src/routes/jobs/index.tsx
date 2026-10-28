import { createFileRoute } from "@tanstack/react-router";
import { JobsScreen } from "@/components/field-screens";
export const Route = createFileRoute("/jobs/")({
  head: () => ({
    meta: [
      { title: "My Jobs | OpsResolve Field" },
      { name: "description", content: "Browse today’s, upcoming and completed service jobs." },
      { property: "og:title", content: "My Jobs | OpsResolve Field" },
      {
        property: "og:description",
        content: "Browse today’s, upcoming and completed service jobs.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: JobsScreen,
});
