import { createFileRoute, redirect } from "@tanstack/react-router";
export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "OpsResolve Field | Technician App" },
      {
        name: "description",
        content: "Field service jobs, guidance and inventory for technicians.",
      },
      { property: "og:title", content: "OpsResolve Field | Technician App" },
      {
        property: "og:description",
        content: "Field service jobs, guidance and inventory for technicians.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  beforeLoad: () => {
    throw redirect({ to: "/home" });
  },
});
