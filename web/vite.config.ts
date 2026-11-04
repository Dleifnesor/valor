// OwO *peeks at the build settings* hewwo! web/vite.config.ts tells Vite (the tool that bundles the web UI) how to
// build ^w^ `npm run build` reads it and writes the finished site to dist/, which the VALOR VM serves through nginx.
// `npm run dev` starts a live dev server that forwards /api calls to a local `valor-web serve` *wags tail*
import { defineConfig } from "vite";
// The React plugin: compiles JSX/TSX and enables fast refresh in the dev server UwU
import react from "@vitejs/plugin-react";

// The VALOR VM serves dist/ through nginx; `npm run dev` proxies the API to a local `valor-web serve`.
// defineConfig just gives editors type hints for the settings object >w<
export default defineConfig({
  // plugins: React support. build: output to dist/, empty it first, no source maps (smaller, and no source shipped),
  // and a higher warning limit for big chunks (the map library is large). server.proxy: in dev, /api goes to
  // port 8080 OwO
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true, sourcemap: false, chunkSizeWarningLimit: 900 },
  server: { proxy: { "/api": "http://127.0.0.1:8080" } },
});
