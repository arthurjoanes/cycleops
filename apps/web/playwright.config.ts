import { defineConfig, devices } from "@playwright/test";

const port = Number(process.env.CYCLEOPS_WEB_TEST_PORT ?? "4188");
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error("Invalid CYCLEOPS_WEB_TEST_PORT");

// E2E against the GitHub Pages build, served under /cycleops/ by a static server that
// behaves like Pages (no SPA fallback, .gz without Content-Encoding).
export default defineConfig({
  testDir: "tests",
  outputDir: "test-results/pages",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${port}/cycleops/`,
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
    viewport: { width: 1440, height: 900 },
    launchOptions: { args: ["--enable-unsafe-swiftshader"] },
  },
  // e2e runs in CI; perf and media write to docs/, so they run by hand.
  projects: [
    { name: "e2e", testMatch: "e2e/**/*.spec.ts" },
    { name: "perf", testMatch: "performance/**/*.spec.ts" },
    { name: "media", testMatch: "media/**/*.spec.ts" },
  ],
  webServer: {
    command: "corepack pnpm build:pages && node scripts/serve-pages.mjs",
    url: `http://127.0.0.1:${port}/cycleops/`,
    env: { PORT: String(port) },
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
