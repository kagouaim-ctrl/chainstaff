import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Served by the FastAPI console at /terminal/; `npm run dev` proxies the API to a local uvicorn.
export default defineConfig({
  base: "/terminal/",
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
  build: {
    target: "es2020",
    chunkSizeWarningLimit: 400,
    rollupOptions: { output: { manualChunks: { vendor: ["react", "react-dom", "react-dom/client", "@tanstack/react-query"] } } },
  },
});
