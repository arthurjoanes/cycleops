import { test } from "@playwright/test";
import { environment, percentile, round, save } from "./environment";

// Lab Web Vitals against the Pages build under /cycleops/, with fixed CPU and
// network throttling. INP comes from a scripted interaction. Lab numbers, not field data.
const RUNS = 3;
const CPU_SLOWDOWN = 4;
const NETWORK = { latency: 150, downloadThroughput: (1.6 * 1024 * 1024) / 8, uploadThroughput: (750 * 1024) / 8 };

declare global {
  interface Window {
    __vitals: { lcp: number; cls: number; events: { name: string; duration: number; interactionId: number }[] };
  }
}

test("lab Web Vitals", async ({ browser }) => {
  test.setTimeout(300_000);
  const runs = [];
  for (let run = 0; run < RUNS; run += 1) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await context.newPage();
    const cdp = await context.newCDPSession(page);
    await cdp.send("Network.enable");
    await cdp.send("Network.setCacheDisabled", { cacheDisabled: true });
    await cdp.send("Network.emulateNetworkConditions", { offline: false, ...NETWORK });
    await cdp.send("Emulation.setCPUThrottlingRate", { rate: CPU_SLOWDOWN });
    await page.addInitScript(() => {
      window.__vitals = { lcp: 0, cls: 0, events: [] };
      new PerformanceObserver((list) => {
        for (const e of list.getEntries()) window.__vitals.lcp = e.startTime;
      }).observe({ type: "largest-contentful-paint", buffered: true });
      new PerformanceObserver((list) => {
        for (const e of list.getEntries() as (PerformanceEntry & { value: number; hadRecentInput: boolean })[]) {
          if (!e.hadRecentInput) window.__vitals.cls += e.value;
        }
      }).observe({ type: "layout-shift", buffered: true });
      new PerformanceObserver((list) => {
        for (const e of list.getEntries() as (PerformanceEntry & { interactionId: number })[]) {
          window.__vitals.events.push({ name: e.name, duration: e.duration, interactionId: e.interactionId });
        }
      }).observe({ type: "event", buffered: true, durationThreshold: 16 } as PerformanceObserverInit);
    });
    await page.goto("./#/?mode=synthetic");
    await page.getByRole("button", { name: "Pausar", exact: true }).waitFor({ timeout: 60_000 });
    await page.waitForTimeout(3_000);
    // Scripted interaction: switch lists, search, back to the queue, open and close details.
    await page.getByRole("button", { name: "Todas as estações" }).click();
    await page.getByLabel("Buscar estação").fill("sintetica 01");
    await page.getByRole("button", { name: "Prioridades" }).click();
    await page.getByRole("button", { name: "Ir para o incidente" }).click();
    await page.getByRole("button", { name: "Fechar" }).click();
    await page.waitForTimeout(1_000);
    const vitals = await page.evaluate(() => window.__vitals);
    const interactions = vitals.events.filter((e) => e.interactionId > 0);
    runs.push({
      lcpMs: round(vitals.lcp, 0),
      cls: round(vitals.cls, 3),
      inpMs: round(Math.max(0, ...interactions.map((e) => e.duration)), 0),
      interactions: interactions.length,
      events: interactions,
    });
    await context.close();
  }
  save("web-vitals.json", {
    environment: environment(browser),
    method: {
      url: "build do Pages servido sob /cycleops/#/?mode=synthetic (serve-pages.mjs)",
      viewport: "1440x900",
      cpuSlowdown: CPU_SLOWDOWN,
      network: { latencyMs: NETWORK.latency, downloadKbps: 1600, uploadKbps: 750 },
      cache: "disabled",
      runs: RUNS,
      interaction:
        "Todas as estações, busca 'sintetica 01', Prioridades, Ir para o incidente, Fechar. INP = maior duração entre os eventos com interactionId.",
    },
    median: {
      lcpMs: round(percentile(runs.map((r) => r.lcpMs), 50), 0),
      cls: round(percentile(runs.map((r) => r.cls), 50), 3),
      inpMs: round(percentile(runs.map((r) => r.inpMs), 50), 0),
    },
    runs,
  });
});
