import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server for dev/preview.html: the panel with mock data, outside GitHub.
export default defineConfig({
  plugins: [react()],
  publicDir: false,
  server: { port: 5174, open: false },
});
