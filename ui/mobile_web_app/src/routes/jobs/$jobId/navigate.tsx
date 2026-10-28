import { createFileRoute } from "@tanstack/react-router";
import { NavigationScreen } from "@/components/field-screens";
export const Route = createFileRoute("/jobs/$jobId/navigate")({
  head: () => ({
    meta: [
      { title: "Navigation | OpsResolve Field" },
      { name: "description", content: "Route to the customer site and arrival confirmation." },
      { property: "og:title", content: "Navigation | OpsResolve Field" },
      {
        property: "og:description",
        content: "Route to the customer site and arrival confirmation.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: () => {
    const { jobId } = Route.useParams();
    return <NavigationScreen id={jobId} />;
  },
});
