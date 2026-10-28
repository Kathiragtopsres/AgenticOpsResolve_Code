import { createFileRoute } from "@tanstack/react-router";
import { LoginScreen } from "@/components/field-screens";
export const Route = createFileRoute("/login")({
  head: () => ({
    meta: [
      { title: "OpsResolve Field | Sign in" },
      { name: "description", content: "Unlock your technician field app." },
      { property: "og:title", content: "OpsResolve Field | Sign in" },
      { property: "og:description", content: "Unlock your technician field app." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: LoginScreen,
});
