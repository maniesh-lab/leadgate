/**
 * Vite config: React + Tailwind, `.env` read from the repo root (only `VITE_*` keys reach
 * the browser), and a dev proxy so `npm run dev` talks to the API on localhost:8000
 * as if both were one site (no CORS needed).
 */
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  envDir: "..",
  server: {
    proxy: { "/api": "http://localhost:8000" },
  },
});
