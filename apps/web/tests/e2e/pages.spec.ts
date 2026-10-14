import { existsSync, readFileSync } from "node:fs";
import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import type { ReplayManifest } from "../../src/lib/contracts";

declare global {
  interface Window {
    __cspViolations: string[];
  }
}

// Every page in these tests records CSP violations.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.__cspViolations = [];
    document.addEventListener("securitypolicyviolation", (e) => {
      window.__cspViolations.push(`${e.violatedDirective} ${e.blockedURI}`);
    });
  });
});

async function open(page: Page, hash = "#/?mode=synthetic") {
  await page.goto(`./${hash}`);
  await expect(page.getByTestId("mode")).toContainText("Simulação");
  await page.getByTitle("Detalhes do replay", { exact: true }).click();
  await expect(page.getByText(/^Evento \d+ de \d+/)).toBeVisible();
}

const details = (page: Page) => page.locator("aside[aria-labelledby='details-title']");

test("opens the accepted recorded replay by default, or the simulation when absent", async ({ page }) => {
  const recorded = existsSync(new URL("../../dist/replay/recorded/manifest.json", import.meta.url));
  await page.goto("./");
  await expect(page.getByTestId("mode")).toContainText(recorded ? "Replay gravado" : "Simulação");
  await page.getByTitle("Detalhes do replay", { exact: true }).click();
  await expect(page.getByText(/^Evento \d+ de \d+/)).toBeVisible();
  await expect(page.getByTestId("connection")).toContainText(recorded ? "arquivo gravado em" : "arquivo local, seed");
  if (recorded) {
    await page.getByRole("button", { name: "Simulação", exact: true }).click();
    await expect(page.getByTestId("mode")).toContainText("Simulação");
    await expect(page.getByTestId("connection")).toHaveText("arquivo local, seed 42");
  }
});

test("opens without login, with the mode and the seed always visible", async ({ page }) => {
  // The Pages build never talks to the API: it has artifacts only.
  const api: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.includes("/api/")) api.push(request.url());
  });
  await open(page);
  await expect(page.getByTestId("mode")).toContainText("seed 42");
  await expect(page.getByTestId("connection")).toHaveText("arquivo local, seed 42");
  await expect(page.getByRole("button", { name: "Simulação", exact: true })).toHaveAttribute("aria-pressed", "true");
  await page.getByTitle("Dados da sessão", { exact: true }).click();
  await expect(page.getByTestId("quality")).toContainText("gerado pelo motor");
  await page.getByRole("button", { name: "Fechar dados da sessão", exact: true }).click();
  await page.locator("footer summary").click();
  await expect(page.getByText(/Norwegian Licence for Open Government Data/)).toBeVisible();
  await expect(page.getByText("© OpenStreetMap contributors").first()).toBeVisible();
  expect(api).toEqual([]);
});

test("pause, 5x, step and go to the incident, with confirmation and resolution", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "Pausar" }).click();
  await expect(page.getByRole("button", { name: "Continuar" })).toBeVisible();
  const before = await page.getByText(/^Evento \d+ de \d+/).textContent();
  await page.getByRole("button", { name: "Passo" }).click();
  await expect(page.getByText(/^Evento \d+ de \d+/)).not.toHaveText(before ?? "");
  await page.getByRole("button", { name: "5×" }).click();
  await expect(page.getByRole("button", { name: "5×" })).toHaveAttribute("aria-pressed", "true");

  await page.getByRole("button", { name: "Ir para o incidente" }).click();
  await expect(details(page)).toBeVisible();
  await expect(details(page)).toContainText("confirmado");
  await page.getByRole("button", { name: "20×" }).click();
  await page.getByRole("button", { name: "Continuar" }).click();
  await expect(details(page)).toContainText("resolvido", { timeout: 45_000 });
});

test("recorded replay confirms and resolves a real incident without API calls or CSP violations", async ({ page }) => {
  const path = new URL("../../dist/replay/recorded/manifest.json", import.meta.url);
  test.skip(!existsSync(path), "No accepted recorded replay has been generated yet.");
  const manifest = JSON.parse(readFileSync(path, "utf8")) as ReplayManifest;
  expect(manifest.mode).toBe("recorded_replay");
  const incident = manifest.incidents
    .filter((i) => i.resolvedSequence !== null && i.resolvedOffsetSeconds !== null)
    .sort((a, b) =>
      ((a.resolvedOffsetSeconds ?? Infinity) - a.confirmedOffsetSeconds) -
      ((b.resolvedOffsetSeconds ?? Infinity) - b.confirmedOffsetSeconds),
    )[0];
  if (!incident || incident.resolvedOffsetSeconds === null) {
    throw new Error("The accepted recording must contain a confirmed and resolved incident.");
  }
  const resolutionMs = Math.ceil((incident.resolvedOffsetSeconds - incident.confirmedOffsetSeconds) * 1000 / 20);
  const resolutionTimeout = resolutionMs + 30_000;
  test.setTimeout(Math.max(90_000, resolutionTimeout + 60_000));

  const api: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.includes("/api/")) api.push(request.url());
  });
  const gzipResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith("/replay/recorded/events.ndjson.gz"),
  );
  await page.goto("./#/?mode=recorded");
  const gzip = await gzipResponse;
  expect(gzip.status()).toBe(200);
  expect(gzip.headers()["content-encoding"]).toBeUndefined();
  await expect(page.getByTestId("mode")).toContainText("Replay gravado");
  await expect(page.getByTestId("connection")).toContainText("arquivo gravado em");
  await page.getByRole("button", { name: "Pausar" }).click();
  await page.getByTitle("Detalhes do replay", { exact: true }).click();
  await page.getByTitle("Explorar incidentes", { exact: true }).click();
  await page.getByLabel("Incidente", { exact: true }).selectOption(incident.incidentId);
  await page.getByRole("button", { name: "Ir para o incidente" }).click();
  await expect(page.getByText(/^Evento \d+ de \d+/)).toContainText(`Evento ${incident.confirmedSequence} de`);

  const kind = incident.kind === "pickup_unavailable" ? "sem bicicletas" : "sem vagas";
  const confirmed = details(page).getByRole("listitem").filter({ hasText: new RegExp(`^${kind}, confirmado`) });
  await expect(confirmed).toHaveCount(1);
  // Keep the same incident in view even when this station has older resolved incidents.
  const identity = (await confirmed.innerText()).match(/\(primeira confirmação às [^)]+\), observado desde \d{2}:\d{2}/)?.[0];
  if (!identity) throw new Error("The confirmed incident must display its original observation and confirmation times.");
  const row = details(page).getByRole("listitem").filter({ hasText: kind }).filter({ hasText: identity });
  await page.getByRole("button", { name: "20×" }).click();
  await page.getByRole("button", { name: "Continuar" }).click();
  await expect(row).toContainText(`${kind}, resolvido`, { timeout: resolutionTimeout });
  await page.locator("canvas.maplibregl-canvas").waitFor();
  expect(api).toEqual([]);
  expect(await page.evaluate(() => window.__cspViolations)).toEqual([]);
});

for (const width of [320, 768, 1440]) {
  test(`no horizontal scroll at ${width} px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 });
    await open(page);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });
}

test("keyboard: open the details from the queue and get focus back on close", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "Pausar" }).click();
  await page.getByRole("button", { name: "Ir para o incidente" }).click();
  await page.getByRole("button", { name: "Fechar" }).click();
  // Paused, so the queue does not reorder under the test; the row is found by its station anyway.
  const queue = page.getByRole("list", { name: "Estações em prioridade" });
  const station = await queue.locator("li[data-station]").first().getAttribute("data-station");
  const row = queue.locator(`li[data-station="${station}"] button`);
  await row.focus();
  await page.keyboard.press("Enter");
  await expect(details(page)).toBeVisible();
  await expect(page.locator("#details-title")).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Fechar" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(details(page)).toBeHidden();
  await expect(row).toBeFocused();
});

test("narrow screen: view buttons, drawer, and focus back after Escape", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await open(page);
  await expect(page.getByRole("button", { name: "Lista", exact: true })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Ir para o incidente" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeHidden();
  await expect(page.getByRole("button", { name: "Ir para o incidente" })).toBeFocused();
  await page.getByRole("button", { name: "Mapa", exact: true }).click();
  await expect(page.getByRole("button", { name: "Mapa", exact: true })).toHaveAttribute("aria-pressed", "true");
});

test("without WebGL the list still works", async ({ page }) => {
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, type: string, ...rest: unknown[]) {
      if (type.startsWith("webgl")) return null;
      return (original as (...args: unknown[]) => RenderingContext | null).call(this, type, ...rest);
    } as typeof HTMLCanvasElement.prototype.getContext;
  });
  await open(page);
  await expect(page.getByText(/Mapa indisponível neste navegador/)).toBeVisible();
  const list = page.getByRole("list", { name: "Todas as estações" });
  await expect(list.getByRole("button").first()).toBeVisible();
  await page.getByLabel("Buscar estação").first().fill("sintetica 001");
  await expect(list.getByRole("button")).toHaveCount(1);
});

test("no CSP violation with the map and the replay running", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "Ir para o incidente" }).click();
  await page.locator("canvas.maplibregl-canvas").waitFor();
  await page.waitForTimeout(2_000);
  expect(await page.evaluate(() => window.__cspViolations)).toEqual([]);
});

// A queue that grows with each batch used to stretch the map and recreate its canvas.
test("the map keeps its size while the queue changes", async ({ page }) => {
  await page.goto("./#/?source=load&n=267&f=1&interval=200&batches=20&seed=7");
  const canvas = page.locator("canvas.maplibregl-canvas");
  await canvas.waitFor();
  const heights = new Set<number>();
  while (!(await page.evaluate(() => window.__cycleopsLoad?.done === true))) {
    heights.add((await canvas.boundingBox())?.height ?? 0);
    await page.waitForTimeout(100);
  }
  expect(heights.size).toBe(1);
});

test("dist/index.html has the CSP meta before any script or link", async () => {
  const html = readFileSync(new URL("../../dist/index.html", import.meta.url), "utf8");
  const csp = html.indexOf('http-equiv="Content-Security-Policy"');
  const firstScript = html.search(/<script|<link/);
  expect(csp).toBeGreaterThan(0);
  expect(csp).toBeLessThan(firstScript);
  expect(html.indexOf("<meta charset")).toBeLessThan(csp);
  expect(html).not.toContain("frame-ancestors");
});

test("axe finds nothing serious or critical", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "Ir para o incidente" }).click();
  await page.getByRole("button", { name: "Pausar" }).click();
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  const blocking = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(blocking.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
});
