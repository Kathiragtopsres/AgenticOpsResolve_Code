import { createFileRoute } from "@tanstack/react-router";
import { CalendarScreen } from "@/components/field-screens";
export const Route = createFileRoute("/calendar")({
  head: () => ({
    meta: [
      { title: "Calendar | OpsResolve Field" },
      {
        name: "description",
        content: "Your schedule, assigned job slots and availability.",
      },
      { property: "og:title", content: "Calendar | OpsResolve Field" },
      {
        property: "og:description",
        content: "Your schedule, assigned job slots and availability.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: CalendarScreen,
});
