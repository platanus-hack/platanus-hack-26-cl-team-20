import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// La página la sirve el servidor local de la voz (:9325). En desarrollo, `npm run dev` le pasa /api.
export default defineConfig({
  plugins: [react()],
  server: { port: 9326, proxy: { "/api": "http://127.0.0.1:9325" } },
  build: { outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 2000 },
});
