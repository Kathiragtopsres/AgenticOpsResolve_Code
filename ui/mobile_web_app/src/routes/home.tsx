import { createFileRoute } from "@tanstack/react-router";
import { HomeScreen } from "@/components/field-screens";
export const Route = createFileRoute("/home")({
  head: () => ({
    meta: [
      { title: "Today’s Jobs | OpsResolve Field" },
      { name: "description", content: "View your field service schedule and next jobs." },
      { property: "og:title", content: "Today’s Jobs | OpsResolve Field" },
      { property: "og:description", content: "View your field service schedule and next jobs." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: HomeScreen,
});
