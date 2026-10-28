import { createFileRoute } from "@tanstack/react-router";
import { ProfileScreen } from "@/components/field-screens";
export const Route = createFileRoute("/profile")({
  head: () => ({
    meta: [
      { title: "Profile & Job History | OpsResolve Field" },
      { name: "description", content: "Your service profile, certifications and completed jobs." },
      { property: "og:title", content: "Profile & Job History | OpsResolve Field" },
      {
        property: "og:description",
        content: "Your service profile, certifications and completed jobs.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: ProfileScreen,
});
