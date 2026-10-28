import { createFileRoute } from "@tanstack/react-router";
import { AssistantScreen } from "@/components/field-screens";
export const Route = createFileRoute("/assistant")({
  head: () => ({
    meta: [
      { title: "Knowledge Assistant | OpsResolve Field" },
      { name: "description", content: "Field diagnostics and service procedures." },
      { property: "og:title", content: "Knowledge Assistant | OpsResolve Field" },
      { property: "og:description", content: "Field diagnostics and service procedures." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: AssistantScreen,
});
