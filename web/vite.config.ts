import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The VALOR VM serves dist/ through nginx; `npm run dev` proxies the API to a local `valor-web serve`.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true, sourcemap: false, chunkSizeWarningLimit: 900 },
  server: { proxy: { "/api": "http://127.0.0.1:8080" } },
});
