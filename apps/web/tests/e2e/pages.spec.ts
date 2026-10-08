import { readFileSync } from "node:fs";
import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";

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

async function open(page: Page, hash = "") {
  await page.goto(`./${hash}`);
  await expect(page.getByTestId("mode")).toContainText("Simulação");
  await expect(page.getByText(/^Evento \d+ de \d+/)).toBeVisible();
}

const details = (page: Page) => page.locator("aside[aria-labelledby='details-title']");

test("opens without login, with the mode and the seed always visible", async ({ page }) => {
  await open(page);
  await expect(page.getByTestId("mode")).toContainText("seed 42");
  await expect(page.getByTestId("connection")).toHaveText("arquivo local, seed 42");
  await expect(page.getByText(/^Simulação: estações e contagens geradas/)).toBeVisible();
  await expect(page.getByText(/Norwegian Licence for Open Government Data/)).toBeVisible();
  await expect(page.getByText("© OpenStreetMap contributors").first()).toBeVisible();
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

test("narrow screen: tabs, drawer, and focus back after Escape", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await open(page);
  await expect(page.getByRole("tab", { name: "Lista" })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("button", { name: "Ir para o incidente" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeHidden();
  await expect(page.getByRole("button", { name: "Ir para o incidente" })).toBeFocused();
  await page.getByRole("tab", { name: "Mapa" }).click();
  await expect(page.getByRole("tab", { name: "Mapa" })).toHaveAttribute("aria-selected", "true");
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
