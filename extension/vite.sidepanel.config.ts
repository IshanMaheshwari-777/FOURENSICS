import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The side panel page loads one classic script (IIFE is fully minified; ES library builds keep whitespace).
// Built after the other bundles.
export default defineConfig({
  plugins: [react()],
  publicDir: false,
  define: { "process.env.NODE_ENV": JSON.stringify("production") },
  build: {
    outDir: "dist",
    emptyOutDir: false,
    lib: {
      entry: "src/sidepanel.tsx",
      formats: ["iife"],
      name: "FourensicsSidePanel",
      fileName: () => "sidepanel.js",
    },
  },
});
