import { execFile } from "node:child_process";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { type APIRequestContext, type BrowserContext, expect, type Page } from "@playwright/test";

const ROOT = fileURLToPath(new URL("../../../../", import.meta.url));
// The same project and files as scripts/checks and CI, from any working directory.
const COMPOSE = ["compose", "-p", "cycleops-e2e", "-f", `${ROOT}compose.yaml`, "-f", `${ROOT}compose.offline.yaml`];

/** docker compose on the e2e project. The worker scenario only when asked for. */
export async function compose(args: string[], scenario?: string): Promise<string> {
  // The e2e project has its own subnet, like in scripts/checks and CI.
  const env: NodeJS.ProcessEnv = { ...process.env, CYCLEOPS_NET: process.env.CYCLEOPS_NET ?? "10.89.2" };
  delete env.CYCLEOPS_SYNTHETIC_SCENARIO;
  if (scenario) env.CYCLEOPS_SYNTHETIC_SCENARIO = scenario;
  const { stdout } = await promisify(execFile)("docker", [...COMPOSE, ...args], {
    env,
    maxBuffer: 64 * 1024 * 1024,
  });
  return stdout;
}

/** Brings every service to the normal configuration: up, healthy, worker without scenario. */
export const converge = () => compose(["up", "-d", "--wait", "--wait-timeout", "180"]);

/** What the page saw, recorded from the first script on: CSP violations, every
 * EventSource with the messages it delivered, and each text of the connection item. */
export interface Probe {
  csp: string[];
  streams: { url: string; at: number; source: EventSource }[];
  messages: { name: string; at: number; url: string; streamId: string | null; reason: string | null; published: string | null }[];
  connection: { text: string | null; at: number }[];
}

declare global {
  interface Window {
    __probe: Probe;
  }
}

function install() {
  const probe: Probe = { csp: [], streams: [], messages: [], connection: [] };
  window.__probe = probe;
  document.addEventListener("securitypolicyviolation", (e) => probe.csp.push(`${e.violatedDirective} ${e.blockedURI}`));
  const Native = window.EventSource;
  window.EventSource = class extends Native {
    constructor(url: string | URL, init?: EventSourceInit) {
      super(url, init);
      probe.streams.push({ url: this.url, at: Date.now(), source: this });
      for (const name of ["station_batch", "heartbeat", "resync_required", "stream_closed"]) {
        // Registered before the app's listeners, so it sees every message the app gets.
        this.addEventListener(name, (message) => {
          let data: Record<string, unknown> = {};
          try {
            data = JSON.parse((message as MessageEvent<string>).data);
          } catch {
            // recorded with nulls
          }
          const health = data.feedHealth as { lastSourceUpdatedAt?: string | null } | undefined;
          probe.messages.push({
            name,
            at: Date.now(),
            url: this.url,
            streamId: (data.streamId as string | undefined) ?? null,
            reason: (data.reason as string | undefined) ?? null,
            published: health?.lastSourceUpdatedAt ?? (data.lastSourceUpdatedAt as string | undefined) ?? null,
          });
        });
      }
    }
  };
  let last: string | null | undefined;
  new MutationObserver(() => {
    const text = document.querySelector('[data-testid="connection"]')?.textContent ?? null;
    if (text !== last) probe.connection.push({ text: (last = text), at: Date.now() });
  }).observe(document, { subtree: true, childList: true, characterData: true });
}

export const instrument = (target: Page | BrowserContext) => target.addInitScript(install);

/** The probe without the EventSource objects, with their readyState instead. */
export const readProbe = (page: Page) =>
  page.evaluate(() => {
    const p = window.__probe;
    return { ...p, streams: p.streams.map((s) => ({ url: s.url, at: s.at, state: s.source.readyState })) };
  });

export const connection = (page: Page) => page.getByTestId("connection");

/** The page on the server stream: the simulation, connected. */
export async function openStream(page: Page, url = "./") {
  await page.goto(url);
  await expect(page.getByTestId("mode")).toContainText("Simulação");
  await expect(connection(page)).toHaveText("conectada", { timeout: 60_000 });
}

/** The active stream and its head, from the snapshot. */
export async function serverHead(request: APIRequestContext) {
  const response = await request.get("api/v1/stations/snapshot");
  expect(response.status()).toBe(200);
  const { streamId, cursor } = (await response.json()) as { streamId: string; cursor: string };
  return { streamId, sequence: Number(cursor.slice(cursor.lastIndexOf("@") + 1)) };
}

// The API allows 60 REST requests per minute per address, shared with the pages.
export const SLOW_POLL = { intervals: [3_000] };
