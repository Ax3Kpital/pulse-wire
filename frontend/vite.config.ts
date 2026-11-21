import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies API calls to the local FastAPI backend. Both bind to loopback only.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8765",
      "/health": "http://127.0.0.1:8765",
    },
  },
  preview: { host: "127.0.0.1" },
  test: { environment: "jsdom", globals: true },
});
