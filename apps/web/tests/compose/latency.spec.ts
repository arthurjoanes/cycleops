import { expect, test } from "@playwright/test";
import { environment, percentile, round, save } from "../performance/environment";
import { compose, openStream } from "./stack";

// Latency through the edge, by hand: CYCLEOPS_MEASURE=1 corepack pnpm e2e:compose latency
// - receive to paint in the browser (window.__cycleopsLatency, #/?measure=1);
// - commit to socket write in the api, from its "commit to write" log lines.
// The api restarts first, so its in-memory sample holds only this page's batches.
const MINUTES = Number(process.env.CYCLEOPS_MEASURE_MINUTES ?? 10);

interface Summary {
  count: number;
  p50: number;
  p95: number;
  max: number;
}

async function commitToWrite(since: string) {
  const lines = await compose(["logs", "api", "--no-color", "--no-log-prefix", "--since", since]);
  return lines
    .split("\n")
    .flatMap((line) => {
      try {
        return [JSON.parse(line) as { time: string; message: string; latency_commit_to_write_ms?: Summary }];
      } catch {
        return [];
      }
    })
    .filter((entry) => entry.message === "commit to write" && entry.latency_commit_to_write_ms)
    .map((entry) => ({ time: entry.time, ...(entry.latency_commit_to_write_ms as Summary) }));
}

test("receive to paint and commit to write over 10 min", async ({ page, browser }) => {
  test.skip(!process.env.CYCLEOPS_MEASURE, "only with CYCLEOPS_MEASURE=1");
  test.setTimeout((MINUTES + 6) * 60_000);

  const restartedAt = new Date().toISOString();
  await compose(["restart", "api"]);
  await compose(["up", "-d", "--wait", "--no-deps", "api"]);
  await openStream(page, "./#/?measure=1");
  const start = new Date();
  // The duration is what is measured here.
  await page.waitForTimeout(MINUTES * 60_000);
  const samples = await page.evaluate(() => window.__cycleopsLatency?.samples ?? []);
  const end = new Date();
  await page.close();
  expect(samples.length).toBeGreaterThan(0);

  // The api logs its summary every 60 s; the first one after the page closed covers it all.
  let summaries: Awaited<ReturnType<typeof commitToWrite>> = [];
  await expect
    .poll(async () => (summaries = await commitToWrite(restartedAt)).some((s) => s.time > end.toISOString()), {
      intervals: [5_000],
      timeout: 90_000,
    })
    .toBe(true);
  const covering = summaries.find((s) => s.time > end.toISOString());

  const window_ = { start: start.toISOString(), end: end.toISOString(), minutes: round((end.getTime() - start.getTime()) / 60_000, 2) };
  save("compose-latency.json", {
    environment: { ...environment(browser, "../.."), cpuThrottling: "none", network: "loopback, no emulation" },
    method:
      "Compose synthetic (compose.offline.yaml) through the edge on 127.0.0.1:8080, one tab on #/?measure=1. " +
      "performance.mark when the station_batch message arrives, performance.measure after the paint " +
      "(requestAnimationFrame, then a MessageChannel task). Includes the 100 ms batching window of the store.",
    window: window_,
    receiveToPaintMs: {
      count: samples.length,
      p50: round(percentile(samples, 50)),
      p95: round(percentile(samples, 95)),
      max: round(Math.max(...samples)),
    },
    samplesMs: samples.map((s) => round(s)),
  });
  save("compose-commit-to-write.json", {
    method:
      "api restarted before the window, then only the measuring tab connected. For each station_batch written " +
      "live to a socket the api keeps write instant minus committedAt (database clock, same host) and logs " +
      "count/p50/p95/max of everything since the restart every 60 s. 'covering' is the first line after the " +
      "tab closed, so it holds the whole window; catch-up events are not measured.",
    apiRestartedAt: restartedAt,
    window: window_,
    covering,
    summaries,
  });
});
