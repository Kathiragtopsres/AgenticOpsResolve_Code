import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";

// Static client-side SPA build (S3-ready).
// Previously this used the TanStack Start SSR plugin. We now use the
// TanStack Router plugin for file-based route codegen only, and Vite emits
// a plain static bundle (index.html + JS/CSS) with no server runtime.
export default defineConfig({
  // amazon-cognito-identity-js references the Node global `global`, which does
  // not exist in the browser. Alias it to `globalThis` so the SDK loads.
  define: {
    global: "globalThis",
  },
  // Resolve TS path aliases (e.g. "@/…") from tsconfig natively (Vite 8+),
  // replacing the deprecated vite-tsconfig-paths plugin.
  resolve: {
    tsconfigPaths: true,
    alias: {
      // The Cognito SDK pulls in Node's `crypto`; give it a browser build.
      crypto: "crypto-browserify",
    },
  },
  plugins: [
    // Tailwind v4 Vite integration.
    tailwindcss(),
    // File-based routing codegen (generates src/routeTree.gen.ts).
    // Must run before the React plugin.
    tanstackRouter({ target: "react", autoCodeSplitting: true }),
    // React fast refresh / JSX transform.
    react(),
  ],
  build: {
    outDir: "dist",
  },
});
