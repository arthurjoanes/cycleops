import { readFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { expect, test } from "@playwright/test";
import type { ReplayManifest, StationBatchEvent } from "../../src/lib/contracts";

const manifest = JSON.parse(readFileSync(new URL("../../public/replay/recorded/manifest.json", import.meta.url), "utf8")) as ReplayManifest;
const incident = manifest.incidents.find((item) => item.resolvedOffsetSeconds !== null && item.resolvedSequence !== null);
if (!incident || incident.resolvedOffsetSeconds === null) throw new Error("The recorded replay needs a resolved incident.");
const events = gunzipSync(readFileSync(new URL("../../public/replay/recorded/events.ndjson.gz", import.meta.url)))
  .toString("utf8").trim().split("\n").map((line) => JSON.parse(line) as StationBatchEvent);
const offsetFor = (sequence: number) => {
  const event = events[sequence - 1];
  if (!event) throw new Error("Incident sequence is absent from the artifact.");
  return (Date.parse(event.committedAt) - Date.parse(manifest.periodStart)) / 1000;
};
const confirmed = offsetFor(incident.confirmedSequence);
const resolved = offsetFor(incident.resolvedSequence ?? 0);
const sharedHash = (at: number, identity = manifest.eventsSha256) =>
  `#/?mode=recorded&station=${incident.stationId}&zone=local&view=all&artifact=${identity}&at=${at}&custom=kept`;

test("copies and restores the exact replay moment with its station and view, without API calls", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const api: string[] = [];
  context.on("request", (request) => {
    if (new URL(request.url()).pathname.includes("/api/")) api.push(request.url());
  });
  await page.goto(`./${sharedHash(confirmed)}`);
  await expect(page.getByRole("button", { name: "Continuar", exact: true })).toBeVisible();
  await expect(page.getByRole("slider", { name: "Linha do tempo" })).toHaveValue(String(confirmed));
  await page.getByTitle("Detalhes do replay", { exact: true }).click();
  await expect(page.getByText(/^Evento \d+ de \d+/)).toContainText(`Evento ${incident.confirmedSequence} de`);
  await page.getByRole("button", { name: "Copiar este momento" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Link copiado" })).toBeVisible();
  const link = await page.evaluate(() => navigator.clipboard.readText());
  const params = new URLSearchParams(new URL(link).hash.slice(3));
  expect(params.get("at")).toBe(String(confirmed));
  expect(params.get("artifact")).toBe(manifest.eventsSha256);
  expect(params.get("station")).toBe(incident.stationId);
  expect(params.get("view")).toBe("all");
  expect(params.get("zone")).toBe("local");
  expect(params.get("custom")).toBe("kept");
  const reopened = await context.newPage();
  await reopened.goto(link);
  await expect(reopened.getByRole("button", { name: "Continuar", exact: true })).toBeVisible();
  await expect(reopened.getByRole("slider", { name: "Linha do tempo" })).toHaveValue(String(confirmed));
  await expect(reopened.locator("aside[aria-labelledby='details-title']")).toBeVisible();
  await reopened.getByTitle("Detalhes do replay", { exact: true }).click();
  await expect(reopened.getByText(/^Evento \d+ de \d+/)).toContainText(`Evento ${incident.confirmedSequence} de`);
  expect(api).toEqual([]);
  await reopened.close();
});

test("back and forward change the paused moment without fetching the artifact again", async ({ page }) => {
  const eventFiles: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.endsWith("/events.ndjson.gz")) eventFiles.push(request.url());
  });
  await page.goto(`./${sharedHash(confirmed)}`);
  const slider = page.getByRole("slider", { name: "Linha do tempo" });
  await expect(slider).toHaveValue(String(confirmed));
  await page.evaluate((hash) => { window.location.hash = hash; }, sharedHash(resolved));
  await expect(slider).toHaveValue(String(resolved));
  await page.getByTitle("Detalhes do replay", { exact: true }).click();
  await expect(page.getByText(/^Evento \d+ de \d+/)).toContainText(`Evento ${incident.resolvedSequence} de`);
  await page.goBack();
  await expect(slider).toHaveValue(String(confirmed));
  await page.goForward();
  await expect(slider).toHaveValue(String(resolved));
  await page.evaluate(() => { window.location.hash += "&tab=map&q=St.+Olav"; });
  await expect(slider).toHaveValue(String(resolved));
  await expect(page.getByRole("button", { name: "Continuar", exact: true })).toBeVisible();
  expect(eventFiles).toHaveLength(1);
});

test("rejects a changed artifact and invalid time instead of opening a different moment silently", async ({ page }) => {
  await page.goto(`./${sharedHash(confirmed, "f".repeat(64))}`);
  await expect(page.getByRole("alert")).toContainText("outra versão");
  await expect(page.getByRole("slider", { name: "Linha do tempo" })).toHaveValue("0");
  await page.getByRole("button", { name: "Abrir do início" }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Continuar", exact: true })).toBeVisible();
  const invalid = sharedHash(confirmed).replace(`at=${confirmed}`, "at=-1");
  await page.evaluate((hash) => { window.location.hash = hash; }, invalid);
  await expect(page.getByRole("alert")).toContainText("inválido");
  await expect(page.getByRole("slider", { name: "Linha do tempo" })).toHaveValue("0");
});

test("timeline supports keyboard and station markers at 320 px without overflow", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await page.goto(`./${sharedHash(confirmed)}`);
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  const slider = page.getByRole("slider", { name: "Linha do tempo" });
  await slider.focus();
  await page.keyboard.press("ArrowRight");
  await expect(slider).toHaveValue(String(Math.round((confirmed + 1) * 1000) / 1000));
  await expect(page.getByRole("button", { name: "Continuar", exact: true })).toBeVisible();
  await page.getByTitle("Explorar incidentes", { exact: true }).click();
  await page.getByRole("button", { name: /^resolvido,/ }).first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(slider).toHaveValue(String(resolved));
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
