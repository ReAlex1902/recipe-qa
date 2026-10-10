import { defineConfig } from "vite";

// In dev, the API runs on :8000 (make dev-api); in production the backend serves dist/.
export default defineConfig({
  server: {
    proxy: { "/ask": "http://localhost:8000", "/healthz": "http://localhost:8000" },
  },
  build: { outDir: "dist", sourcemap: false },
});
