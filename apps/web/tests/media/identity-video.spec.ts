import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import type { ReplayManifest } from "../../src/lib/contracts";

const OUT = fileURLToPath(new URL("../../../../docs/media/identity-demo.webm", import.meta.url));
const SIZE = { width: 1440, height: 1000 };

declare global {
  interface Window {
    __identityVideoCsp: string[];
  }
}

// Run by hand: corepack pnpm exec playwright test --project=media tests/media/identity-video.spec.ts
test("identity video: real replay from confirmation to resolution", async ({ browser }, testInfo) => {
  const manifest = JSON.parse(readFileSync(new URL("../../dist/replay/recorded/manifest.json", import.meta.url), "utf8")) as ReplayManifest;
  expect(manifest.mode).toBe("recorded_replay");
  const resolved = manifest.incidents
    .filter((i) => i.resolvedSequence !== null && i.resolvedOffsetSeconds !== null)
    .sort((a, b) =>
      ((a.resolvedOffsetSeconds ?? Infinity) - a.confirmedOffsetSeconds) -
      ((b.resolvedOffsetSeconds ?? Infinity) - b.confirmedOffsetSeconds),
    );
  const incident = resolved.find((i) => i.stationId === "412") ?? resolved[0];
  if (!incident || incident.resolvedOffsetSeconds === null) {
    throw new Error("The recording must contain a confirmed and resolved incident.");
  }
  const resolutionMs = Math.ceil((incident.resolvedOffsetSeconds - incident.confirmedOffsetSeconds) * 1000 / 20);
  const resolutionTimeout = resolutionMs + 30_000;
  test.setTimeout(Math.max(90_000, resolutionTimeout + 60_000));

  const baseURL = testInfo.project.use.baseURL;
  if (!baseURL) throw new Error("The Pages base URL is required.");
  const origin = new URL(baseURL).origin;
  const forbidden: string[] = [];
  const errors: string[] = [];
  const context = await browser.newContext({
    baseURL,
    viewport: SIZE,
    recordVideo: { dir: testInfo.outputPath("video"), size: SIZE },
  });
  await context.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== origin || url.pathname.includes("/api/")) {
      forbidden.push(url.href);
      await route.abort();
      return;
    }
    await route.continue();
  });
  const page = await context.newPage();
  const video = page.video();
  if (!video) throw new Error("Video recording must be enabled.");
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  await page.addInitScript(() => {
    window.__identityVideoCsp = [];
    document.addEventListener("securitypolicyviolation", (event) => {
      window.__identityVideoCsp.push(`${event.violatedDirective} ${event.blockedURI}`);
    });
  });

  try {
    await page.goto("./#/?mode=recorded");
    await expect(page.getByTestId("mode")).toContainText("Replay gravado");
    await expect(page.getByTestId("connection")).toContainText("arquivo gravado em");
    await page.locator("canvas.maplibregl-canvas").waitFor();
    await page.evaluate(() => document.fonts.ready);
    // These holds give viewers time to read the current screen.
    await page.waitForTimeout(2_000);
    await page.getByRole("button", { name: "Pausar" }).click();
    await page.waitForTimeout(1_000);
    await page.getByLabel("Incidente", { exact: true }).selectOption(incident.incidentId);
    await page.getByRole("button", { name: "Ir para o incidente" }).click();
    await expect(page.getByText(/^Evento \d+ de \d+/)).toContainText(`Evento ${incident.confirmedSequence} de`);

    const details = page.locator("aside[aria-labelledby='details-title']");
    const kind = incident.kind === "pickup_unavailable" ? "sem bicicletas" : "sem vagas";
    const confirmed = details.getByRole("listitem").filter({ hasText: new RegExp(`^${kind}, confirmado`) });
    await expect(confirmed).toHaveCount(1);
    const identity = (await confirmed.innerText()).match(/\(primeira confirmação às [^)]+\), observado desde \d{2}:\d{2}/)?.[0];
    if (!identity) throw new Error("The incident must show its original observation and confirmation times.");
    const row = details.getByRole("listitem").filter({ hasText: kind }).filter({ hasText: identity });
    await expect(row).toBeInViewport();
    await page.waitForTimeout(2_000);
    await page.screenshot({ path: testInfo.outputPath("identity-confirmed.png") });

    await page.getByRole("button", { name: "20×" }).click();
    await page.getByRole("button", { name: "Continuar" }).click();
    await expect(row).toContainText(`${kind}, resolvido`, { timeout: resolutionTimeout });
    await page.getByRole("button", { name: "Pausar" }).click();
    await expect(row).toBeInViewport();
    await page.waitForTimeout(3_000);
    await page.screenshot({ path: testInfo.outputPath("identity-resolved.png") });
    expect(forbidden).toEqual([]);
    expect(errors).toEqual([]);
    expect(await page.evaluate(() => window.__identityVideoCsp)).toEqual([]);
  } finally {
    await context.close();
  }
  await video.saveAs(OUT);
});
