import { defineConfig } from "vite";

// The background service worker is declared with "type": "module" in the manifest.
export default defineConfig({
  publicDir: false,
  build: {
    outDir: "dist",
    emptyOutDir: false,
    lib: {
      entry: "src/background.ts",
      formats: ["es"],
      fileName: () => "background.js",
    },
  },
});
