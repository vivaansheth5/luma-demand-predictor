import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { viteSingleFile } from "vite-plugin-singlefile";

// `vite build` produces a normal static site (GitHub Pages, FastAPI).
// `vite build --mode single` inlines every asset into one HTML file.
export default defineConfig(({ mode }) => ({
  base: "./",
  plugins: [react(), ...(mode === "single" ? [viteSingleFile()] : [])],
  build: {
    outDir: mode === "single" ? "dist-single" : "dist",
    chunkSizeWarningLimit: 900,
  },
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
}));
