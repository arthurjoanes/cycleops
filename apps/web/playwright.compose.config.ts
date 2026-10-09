import { defineConfig, devices } from "@playwright/test";

// E2E against the Compose stack (synthetic, compose.offline.yaml, project cycleops-e2e)
// through the edge on 127.0.0.1:8080. No webServer: the suite brings the stack up itself
// and stops, restarts and resets its services. One worker: every tab arrives from the
// same address, and the per-address stream limit is part of what is tested.
export default defineConfig({
  testDir: "tests/compose",
  outputDir: "test-results/compose",
  timeout: 240_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: "http://127.0.0.1:8080/",
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
    viewport: { width: 1440, height: 900 },
    launchOptions: { args: ["--enable-unsafe-swiftshader"] },
  },
  projects: [{ name: "compose", testMatch: "**/*.spec.ts" }],
});
