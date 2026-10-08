import { expect, test } from "@playwright/test";
import { environment, percentile, round, save } from "./environment";

// Frontend load: seeded batches straight into the store, no network. The 15 s
// cadence is compressed to 1 s; the cost of one batch does not depend on the cadence.
const SCENARIOS = [
  { name: "267 estações, todas mudando a cada lote", n: 267, f: 1 },
  { name: "1.000 estações, 30% mudando a cada lote", n: 1000, f: 0.3 },
] as const;
const VIEWS = ["priorities", "all"] as const;
const BATCHES = 30;
const WARMUP = 5;
const CPU_SLOWDOWN = 4;

interface Sample {
  sequence: number;
  changed: number;
  applyMs: number;
  renderMs: number;
  paintMs: number;
  rowRenders: number;
}

declare global {
  interface Window {
    __longtasks: { start: number; duration: number }[];
    __frames: number;
  }
}

const results: unknown[] = [];

test.describe.configure({ mode: "serial" });

for (const scenario of SCENARIOS) {
  for (const view of VIEWS) {
    test(`load: ${scenario.name}, view ${view}`, async ({ page }) => {
      test.setTimeout(240_000);
      const cdp = await page.context().newCDPSession(page);
      await cdp.send("Emulation.setCPUThrottlingRate", { rate: CPU_SLOWDOWN });
      await page.addInitScript(() => {
        window.__longtasks = [];
        new PerformanceObserver((list) => {
          for (const e of list.getEntries()) window.__longtasks.push({ start: e.startTime, duration: e.duration });
        }).observe({ type: "longtask", buffered: true });
        window.__frames = 0;
        const loop = () => {
          window.__frames += 1;
          requestAnimationFrame(loop);
        };
        requestAnimationFrame(loop);
      });
      const query = `source=load&n=${scenario.n}&f=${scenario.f}&interval=1000&batches=${BATCHES}&seed=7${view === "all" ? "&view=all" : ""}`;
      await page.goto(`./#/?${query}`);
      await page.waitForFunction((w) => (window.__cycleopsLoad?.samples.length ?? 0) >= w, WARMUP);
      await cdp.send("HeapProfiler.collectGarbage");
      const heapWarm = (await cdp.send("Runtime.getHeapUsage")).usedSize;
      const warm = await page.evaluate(() => ({ t: performance.now(), frames: window.__frames }));
      await page.waitForFunction(() => window.__cycleopsLoad?.done === true, null, { timeout: 200_000 });
      const end = await page.evaluate(() => ({ t: performance.now(), frames: window.__frames }));
      await cdp.send("HeapProfiler.collectGarbage");
      const heapEnd = (await cdp.send("Runtime.getHeapUsage")).usedSize;
      const data = await page.evaluate(() => ({
        samples: window.__cycleopsLoad?.samples ?? [],
        longtasks: window.__longtasks,
        domNodes: document.getElementsByTagName("*").length,
      }));
      const samples = (data.samples as Sample[]).filter((s) => s.sequence > WARMUP);
      const tasks = data.longtasks.filter((t) => t.start >= warm.t && t.start <= end.t);
      const durationS = (end.t - warm.t) / 1000;
      const summary = {
        scenario: scenario.name,
        view,
        stations: scenario.n,
        fraction: scenario.f,
        batchesMeasured: samples.length,
        changedPerBatch: round(samples.reduce((t, s) => t + s.changed, 0) / samples.length, 0),
        applyMs: { p50: round(percentile(samples.map((s) => s.applyMs), 50)), p95: round(percentile(samples.map((s) => s.applyMs), 95)), max: round(Math.max(...samples.map((s) => s.applyMs))) },
        renderMs: { p50: round(percentile(samples.map((s) => s.renderMs), 50)), p95: round(percentile(samples.map((s) => s.renderMs), 95)), max: round(Math.max(...samples.map((s) => s.renderMs))) },
        receiveToPaintMs: { p50: round(percentile(samples.map((s) => s.paintMs), 50)), p95: round(percentile(samples.map((s) => s.paintMs), 95)), max: round(Math.max(...samples.map((s) => s.paintMs))) },
        rowRendersPerBatch: { p50: percentile(samples.map((s) => s.rowRenders), 50), max: Math.max(...samples.map((s) => s.rowRenders)) },
        longTasks: { count: tasks.length, maxMs: round(Math.max(0, ...tasks.map((t) => t.duration))) },
        framesPerSecond: round((end.frames - warm.frames) / durationS, 0),
        domNodes: data.domNodes,
        heapMb: { afterWarmup: round(heapWarm / 1024 ** 2), end: round(heapEnd / 1024 ** 2) },
        raw: { samples: data.samples, longtasks: tasks },
      };
      results.push(summary);
      expect(samples.length).toBeGreaterThanOrEqual(BATCHES - WARMUP - 1);
    });
  }
}

test.afterAll(async ({ browser }) => {
  save("frontend-load.json", {
    environment: environment(browser),
    method: {
      batches: BATCHES,
      warmupBatches: WARMUP,
      intervalMs: 1000,
      cpuSlowdown: CPU_SLOWDOWN,
      network: "none (events injected in the page)",
      seed: 7,
      notes: "applyMs: store update for the batch; renderMs: store update plus the React render and commit (the list); the map updates in its own animation frame and is not in renderMs; receiveToPaintMs: from the batch leaving the generator to the first task after the next frame (100 ms coalescing window included).",
    },
    results,
  });
});
