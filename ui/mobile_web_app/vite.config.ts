import { copyFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";

// SPA deep-link fallback: duplicate index.html as 404.html so static hosts
// (S3 website hosting, GitHub Pages, etc.) serve the app shell for unknown
// client-side routes like /jobs/123 on direct load or refresh.
function spaFallback(): Plugin {
  return {
    name: "spa-404-fallback",
    apply: "build",
    closeBundle() {
      const outDir = resolve(__dirname, "dist");
      const index = resolve(outDir, "index.html");
      if (existsSync(index)) {
        copyFileSync(index, resolve(outDir, "404.html"));
      }
    },
  };
}

export default defineConfig({
  // amazon-cognito-identity-js references Node's `global`, which doesn't exist
  // in the browser. Alias it to `globalThis` so the module loads under Vite.
  define: {
    global: "globalThis",
  },
  plugins: [
    // Generates routeTree.gen.ts from the file-based routes in src/routes.
    tanstackRouter({
      target: "react",
      autoCodeSplitting: true,
    }),
    react(),
    tailwindcss(),
    spaFallback(),
  ],
  resolve: {
    // Native tsconfig "paths" resolution (replaces vite-tsconfig-paths).
    tsconfigPaths: true,
  },
  build: {
    outDir: "dist",
  },
});
