import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// MV3 content scripts cannot be ES modules, so the content script is one IIFE bundle.
export default defineConfig({
  plugins: [react()],
  publicDir: "public",
  define: { "process.env.NODE_ENV": JSON.stringify("production") },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    lib: {
      entry: "src/content.tsx",
      formats: ["iife"],
      name: "EvidenceTriageContent",
      fileName: () => "content.js",
    },
  },
});
