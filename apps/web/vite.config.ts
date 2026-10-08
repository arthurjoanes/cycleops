import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";

// Same policy as the Caddy header, without frame-ancestors (it does nothing in a meta tag).
export const CSP_META =
  "default-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data: blob:; " +
  "style-src 'self' 'unsafe-inline'; font-src 'self'; worker-src 'self'; object-src 'none'; " +
  "base-uri 'none'; form-action 'self'";

/** The Pages build gets the CSP as the first element of <head>, right after the charset. */
function cspMeta(): Plugin {
  return {
    name: "cycleops-csp-meta",
    transformIndexHtml: {
      order: "post",
      handler(html) {
        const tag = `<meta http-equiv="Content-Security-Policy" content="${CSP_META}" />`;
        return html.replace(/<meta charset="UTF-8" \/>/, (charset) => `${charset}\n    ${tag}`);
      },
    },
  };
}

// Dev server only: the API behind the local Compose edge, on the same origin as the page
// (no CORS).
const EDGE = "http://127.0.0.1:8080";

export default defineConfig(({ mode, command }) => ({
  // GitHub Pages serves the project under /cycleops/; Caddy serves it at the root.
  base: mode === "pages" ? "/cycleops/" : "/",
  // Hash routing: no SPA fallback, so a missing file is a 404 as on GitHub Pages.
  appType: "mpa",
  plugins: [react(), mode === "pages" ? cspMeta() : null],
  resolve: {
    alias: {
      "@contracts": fileURLToPath(new URL("../../packages/contracts", import.meta.url)),
    },
  },
  server: {
    fs: { allow: ["../.."] },
    proxy: command === "serve" ? { "/api": EDGE, "/health": EDGE } : undefined,
  },
  build: { manifest: true, target: "es2022" },
  test: {
    environment: "jsdom",
    include: ["tests/unit/**/*.test.{ts,tsx}"],
    setupFiles: ["tests/unit/setup.ts"],
  },
}));
