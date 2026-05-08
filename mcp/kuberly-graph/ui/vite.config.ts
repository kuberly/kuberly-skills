import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev we serve the SPA from Vite (:5173) and proxy /api/* to the
// kuberly-graph MCP server (default :8000). VITE_API_BASE overrides this.
//
// In prod the SPA is built to ./dist and served by nginx alongside a
// reverse-proxy to the MCP server's /api/v1/* — see Dockerfile.ui.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_BASE || "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
    target: "es2022",
    rollupOptions: {
      output: {
        // Split the heavy 3D bundle so the Dashboard tab loads instantly.
        manualChunks: {
          three: ["three"],
          forcegraph: ["react-force-graph-3d"],
        },
      },
    },
  },
});
