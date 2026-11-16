import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { getDevPorts } from "../scripts/dev-ports.mjs";

export default defineConfig(({ command }) => {
  // Only the dev server consults the per-checkout port registry.
  const ports =
    command === "serve" ? getDevPorts({ cwd: import.meta.dirname }) : undefined;
  const djangoTarget =
    process.env.VITE_DJANGO_ORIGIN ?? ports?.backendOrigin ?? "http://127.0.0.1:8000";

  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        "@": path.resolve(import.meta.dirname, "./src"),
      },
    },
    define: {
      __DJANGO_ORIGIN__: JSON.stringify(djangoTarget),
    },
    server: {
      port: ports?.frontendPort ?? 5173,
      strictPort: true,
      proxy: {
        "/api": {
          target: djangoTarget,
          changeOrigin: true,
        },
      },
    },
  };
});
