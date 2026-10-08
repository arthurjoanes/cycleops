import { get, type IncomingMessage } from "node:http";
import { gunzipSync } from "node:zlib";
import { expect, type Page, test } from "@playwright/test";
import {
  compose,
  connection,
  converge,
  instrument,
  openStream,
  readProbe,
  serverHead,
  SLOW_POLL,
} from "./stack";
import { startProxy } from "./tcpProxy";

// Against the Compose stack through the edge. The tests run in this order, and the last
// one resets the database.

const CSP =
  "default-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data: blob:; " +
  "style-src 'self' 'unsafe-inline'; font-src 'self'; worker-src 'self'; object-src 'none'; " +
  "base-uri 'none'; frame-ancestors 'none'; form-action 'self'";
// The header's publication time (pt-BR, Oslo, with seconds).
const OSLO = new Intl.DateTimeFormat("pt-BR", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
  timeZone: "Europe/Oslo",
});
const STALE_AFTER_MS = 60_000;
const HEARTBEAT_MS = 15_000;
const WATCHDOG_MS = 40_000;

// Also puts back what a failed test left behind (a stopped worker, the quarantine scenario).
test.beforeAll(async () => {
  await converge();
});

test.beforeEach(async ({ context }) => {
  await instrument(context);
});

const quality = (page: Page) => page.getByTestId("quality");

const queue = (page: Page) => page.getByRole("list", { name: "Estações em prioridade" });

/** A fresh stream starts with an empty queue; incidents confirm after a few publications. */
const queueHasRows = (page: Page) => expect(queue(page).locator("li").first()).toBeVisible({ timeout: 180_000 });

/** Unverifiable incidents keep their place, so the rows stay, shown as old data. */
async function expectQueueDimmed(page: Page) {
  await expect(page.getByText(/A fila é do último dado válido\.$/)).toBeVisible();
  await expect(queue(page).locator("li").first()).toBeVisible();
  await expect(queue(page)).toHaveCSS("filter", "grayscale(1)");
}

/** Response headers and the first chunk of body, then the socket is dropped. */
function firstChunk(path: string, headers: Record<string, string>) {
  return new Promise<{ response: IncomingMessage; body: Buffer }>((resolve, reject) => {
    const request = get(`http://127.0.0.1:8080${path}`, { headers }, (response) => {
      response.once("data", (body: Buffer) => {
        resolve({ response, body });
        request.destroy();
      });
    });
    request.on("error", reject);
    request.setTimeout(30_000, () => request.destroy(new Error(`${path}: no data in 30 s`)));
  });
}

test("opens the simulation over SSE, connected", async ({ page, request }) => {
  await openStream(page);
  await expect(page.getByTestId("mode")).toContainText("seed 42");
  await expect(page.getByRole("button", { name: "Servidor" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText(/^Evento \d+ de \d+/)).toHaveCount(0);
  const { streamId } = await serverHead(request);
  // Data arrives through the stream of the server's active stream.
  await expect
    .poll(async () => (await readProbe(page)).messages.some((m) => m.name === "station_batch" && m.streamId === streamId), {
      timeout: 60_000,
    })
    .toBe(true);
  const { streams } = await readProbe(page);
  expect(streams).toHaveLength(1);
  expect(streams[0]?.url).toContain(`/api/v1/stream/stations?after=${encodeURIComponent(`${streamId}@`)}`);
});

test("edge headers: CSP, nosniff, referrer, permissions, no-cache on index.html", async ({ request }) => {
  const response = await request.get("./");
  expect(response.status()).toBe(200);
  const headers = response.headers();
  expect(headers["content-type"]).toContain("text/html");
  expect(headers["content-security-policy"]).toBe(CSP);
  expect(headers["x-content-type-options"]).toBe("nosniff");
  expect(headers["referrer-policy"]).toBe("strict-origin-when-cross-origin");
  expect(headers["permissions-policy"]).toMatch(/camera=\(\).*geolocation=\(\).*microphone=\(\)/);
  expect(headers["cache-control"]).toBe("no-cache");
});

test("the SSE response through the edge has no Content-Encoding", async () => {
  const encoding = { "Accept-Encoding": "gzip, br" };
  // The same header does compress the other API routes, so it is honored on this edge.
  const snapshot = await firstChunk("/api/v1/stations/snapshot", encoding);
  expect(snapshot.response.headers["content-encoding"]).toBe("gzip");

  const { response, body } = await firstChunk("/api/v1/stream/stations", encoding);
  expect(response.statusCode).toBe(200);
  expect(response.headers["content-type"]).toMatch(/^text\/event-stream/);
  expect(response.headers["content-encoding"]).toBeUndefined();
  expect(response.headers["cache-control"]).toBe("no-store, no-transform");
  expect(body.toString("utf8")).toMatch(/^(event|data|id|retry):/m);
});

test("no CSP violation with the stream and the map running", async ({ page }) => {
  await openStream(page);
  await page.locator("canvas.maplibregl-canvas").waitFor();
  const mapAt = Date.now();
  // A batch after the map is up: the page has drawn live data on it.
  await expect
    .poll(async () => (await readProbe(page)).messages.some((m) => m.name === "station_batch" && m.at > mapAt), {
      timeout: 60_000,
    })
    .toBe(true);
  await page.getByRole("button", { name: "Todas as estações" }).click();
  await page.getByRole("list", { name: "Todas as estações" }).getByRole("button").first().click();
  await expect(page.locator("aside[aria-labelledby='details-title']")).toBeVisible();
  expect((await readProbe(page)).csp).toEqual([]);
});

test("switching between the server stream and an artifact does not mix events", async ({ page, request }) => {
  await openStream(page);
  const server = await serverHead(request);
  const publication = page.getByTestId("publication");

  // Server -> artifact.
  await page.getByRole("button", { name: "Simulação (arquivo)" }).click();
  await expect(connection(page)).toHaveText("arquivo local, seed 42");
  // The artifact session is on screen, so the stream session has been torn down.
  const leftAt = Date.now();
  await expect(page.getByTestId("mode")).toContainText("Simulação");
  await expect(page.getByRole("button", { name: "Servidor" })).toHaveAttribute("aria-pressed", "false");
  const counter = page.getByText(/^Evento \d+ de \d+/);
  await expect(counter).toBeVisible();
  // 2: EventSource.CLOSED.
  expect((await readProbe(page)).streams.map((s) => s.state)).toEqual([2]);
  // Paused after a step (which flushes the batching window), the header shows the
  // artifact's publication for the event on the counter.
  const events = gunzipSync(await (await request.get("replay/synthetic/events.ndjson.gz")).body())
    .toString("utf8")
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line) as { sourceUpdatedAt: string | null; feedHealth: { lastSourceUpdatedAt: string | null } });
  await page.getByRole("button", { name: "Pausar" }).click();
  await page.getByRole("button", { name: "Passo" }).click();
  const artifactPublication = async () => {
    const n = Number(/^Evento (\d+)/.exec((await counter.textContent()) ?? "")?.[1]);
    const event = events[n - 1];
    const at = event?.feedHealth.lastSourceUpdatedAt ?? event?.sourceUpdatedAt;
    return at ? `${OSLO.format(new Date(at))} ` : null;
  };
  await expect.poll(async () => (await publication.textContent())?.startsWith((await artifactPublication()) ?? "-")).toBe(true);
  const shown = { counter: await counter.textContent(), publication: await publication.textContent() };
  // Meanwhile the server commits new events; none reaches this page.
  await expect.poll(async () => (await serverHead(request)).sequence, { ...SLOW_POLL, timeout: 90_000 }).toBeGreaterThan(server.sequence + 1);
  expect((await readProbe(page)).messages.filter((m) => m.at >= leftAt)).toEqual([]);
  await expect(counter).toHaveText(shown.counter ?? "");
  await expect(publication).toHaveText(shown.publication ?? "");

  // Artifact (playing) -> server: a new snapshot, and the stream from its cursor.
  await page.getByRole("button", { name: "Continuar" }).click();
  const snapshot = page.waitForResponse((r) => r.url().includes("/api/v1/stations/snapshot"));
  await page.getByRole("button", { name: "Servidor" }).click();
  const backAt = Date.now();
  const { streamId, cursor } = (await (await snapshot).json()) as { streamId: string; cursor: string };
  expect(streamId).toBe(server.streamId);
  await expect(connection(page)).toHaveText("conectada");
  await expect(counter).toHaveCount(0);
  const reopened = (await readProbe(page)).streams.filter((s) => s.at >= backAt);
  expect(reopened.map((s) => new URL(s.url).searchParams.get("after"))).toEqual([cursor]);
  // The next batch is the server's, and the header shows the server's publication.
  await expect
    .poll(async () => (await readProbe(page)).messages.filter((m) => m.at >= backAt && m.name === "station_batch").length, {
      timeout: 60_000,
    })
    .toBeGreaterThan(0);
  const received = (await readProbe(page)).messages.filter((m) => m.at >= backAt);
  expect(new Set(received.map((m) => m.streamId))).toEqual(new Set([server.streamId]));
  await expect
    .poll(async () => {
      const latest = (await readProbe(page)).messages.findLast((m) => m.published)?.published;
      return latest ? (await publication.textContent())?.startsWith(`${OSLO.format(new Date(latest))} `) : false;
    })
    .toBe(true);
});

test("five tabs with forged X-Forwarded-For: the oldest is superseded", async ({ context }) => {
  const tabs: Page[] = [];
  const forged = new Map<Page, string[]>();
  for (let i = 1; i <= 5; i += 1) {
    const tab = await context.newPage();
    await tab.setExtraHTTPHeaders({ "X-Forwarded-For": `203.0.113.${i}` });
    forged.set(tab, []);
    tab.on("request", (r) => {
      if (r.url().includes("/api/v1/stream/stations")) {
        void r.allHeaders().then((h) => forged.get(tab)?.push(h["x-forwarded-for"] ?? ""));
      }
    });
    await openStream(tab);
    tabs.push(tab);
  }
  const [oldest, ...rest] = tabs as [Page, ...Page[]];
  await expect(connection(oldest)).toHaveText("encerrada");
  await expect(oldest.getByText(/^O servidor encerrou esta conexão/)).toBeVisible();
  const closed = (await readProbe(oldest)).messages.filter((m) => m.name === "stream_closed");
  expect(closed.map((m) => m.reason)).toEqual(["superseded"]);
  for (const tab of rest) await expect(connection(tab)).toHaveText("conectada");
  // Each stream request did carry its own forged address.
  expect(tabs.map((tab) => forged.get(tab)?.[0])).toEqual([1, 2, 3, 4, 5].map((i) => `203.0.113.${i}`));
});

test("a frozen connection turns suspect within 40 s and reconnects", async ({ page }) => {
  const proxy = await startProxy(8080);
  try {
    await openStream(page, `http://127.0.0.1:${proxy.port}/`);
    proxy.freeze();
    const frozenAt = Date.now();
    const after = async (text: string, since: number) =>
      (await readProbe(page)).connection.find((c) => c.text === text && c.at >= since)?.at ?? null;
    await expect.poll(() => after("suspeita", frozenAt), { timeout: WATCHDOG_MS + 10_000 }).not.toBeNull();
    const suspectAt = (await after("suspeita", frozenAt)) as number;
    // 40 s counted from the last message, which came before the freeze; 1 s for the timer
    // and the render.
    expect(suspectAt - frozenAt).toBeLessThanOrEqual(WATCHDOG_MS + 1_000);
    await expect.poll(() => after("conectada", suspectAt), { timeout: 120_000 }).not.toBeNull();
    await expect(connection(page)).toHaveText("conectada");
    // Data flows again on the new connection.
    const reconnectedAt = (await after("conectada", suspectAt)) as number;
    await expect
      .poll(async () => (await readProbe(page)).messages.some((m) => m.at > reconnectedAt), { timeout: 60_000 })
      .toBe(true);
  } finally {
    await page.close();
    await proxy.close();
  }
});

test("with the worker stopped the page shows the source stale", async ({ page }) => {
  await openStream(page);
  await expect(quality(page)).toContainText("fonte em dia", { timeout: 60_000 });
  await queueHasRows(page);
  await expect(queue(page)).toHaveCSS("filter", "none");
  await compose(["stop", "worker"]);
  const stoppedAt = Date.now();
  try {
    await expect(quality(page)).toContainText("fonte vencida", { timeout: STALE_AFTER_MS + 2 * HEARTBEAT_MS });
    expect(Date.now() - stoppedAt).toBeLessThanOrEqual(STALE_AFTER_MS + 2 * HEARTBEAT_MS);
    await expect(page.getByText(/^Fonte vencida desde/)).toBeVisible();
    await expectQueueDimmed(page);
    await expect(connection(page)).toHaveText("conectada");
  } finally {
    await compose(["up", "-d", "--wait", "--no-deps", "worker"]);
  }
});

test("worker with contract_quarantine: quarantine and a dimmed queue", async ({ page }) => {
  await openStream(page);
  await queueHasRows(page);
  await compose(["up", "-d", "--wait", "--no-deps", "worker"], "contract_quarantine");
  try {
    await expect(quality(page)).toContainText("fonte em quarentena", { timeout: 120_000 });
    await expect(page.getByText(/^O formato do feed mudou e os dados estão em quarentena desde/)).toBeVisible();
    await expectQueueDimmed(page);
  } finally {
    await compose(["up", "-d", "--wait", "--no-deps", "worker"]);
  }
});

test("no outbound connection from the worker with compose.offline.yaml", async () => {
  const script = "import socket; socket.create_connection(('1.1.1.1', 443), timeout=5)";
  await expect(compose(["exec", "-T", "worker", "python", "-c", script])).rejects.toThrow(/OSError|timed out/);
});

// Last: wipes the database. The page stays open across down -v and up.
test("a database reset with the page open leads to a resync on the new stream", async ({ page, request }) => {
  await openStream(page);
  const before = await serverHead(request);
  const resetAt = Date.now();
  await compose(["down", "-v"]);
  await converge();
  // The worker creates the new stream right after it starts.
  await expect
    .poll(async () => (await request.get("api/v1/stations/snapshot")).status(), { ...SLOW_POLL, timeout: 60_000 })
    .toBe(200);
  const now = await serverHead(request);
  expect(now.streamId).not.toBe(before.streamId);

  await expect
    .poll(async () => (await readProbe(page)).streams.at(-1)?.url ?? "", { timeout: 120_000 })
    .toContain(`after=${encodeURIComponent(`${now.streamId}@`)}`);
  await expect(connection(page)).toHaveText("conectada");
  const { messages } = await readProbe(page);
  expect(messages.some((m) => m.at >= resetAt && m.name === "resync_required")).toBe(true);
  await expect
    .poll(async () => (await readProbe(page)).messages.some((m) => m.at >= resetAt && m.streamId === now.streamId), {
      timeout: 60_000,
    })
    .toBe(true);
  await expect(page.getByTestId("mode")).toContainText("Simulação");
});
