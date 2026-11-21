// @ts-check
import { defineConfig } from "astro/config";
import tailwindcss from "@tailwindcss/vite";

// Sitio estático: Render lo sirve como "static site" (pcx-web en docs/arquitectura/ARQUITECTURA-ACTUAL.md).
export default defineConfig({
  output: "static",
  server: { port: 9300, host: "127.0.0.1" },
  build: { inlineStylesheets: "always" },
  vite: { plugins: [tailwindcss()] },
});
