import { fileURLToPath } from "node:url";
import { expect, type Page, test } from "@playwright/test";

const OUT = new URL("../../../../docs/media/", import.meta.url);

async function openRecorded(page: Page) {
  await page.goto("./#/?mode=recorded");
  await expect(page.getByTestId("mode")).toContainText("Replay gravado");
  await page.getByRole("button", { name: "Pausar" }).click();
  await page.getByRole("button", { name: "Ir para o incidente" }).click();
  await expect(page.getByText("St. Olavs gate", { exact: true }).first()).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
}

test("identity: recorded desktop", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await openRecorded(page);
  await page.locator("canvas.maplibregl-canvas").waitFor();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: fileURLToPath(new URL("identity-desktop.png", OUT)), fullPage: true });
});

test("identity: recorded mobile", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  await openRecorded(page);
  await page.getByRole("button", { name: "Fechar" }).click();
  await page.getByRole("button", { name: "Mapa", exact: true }).click();
  await page.locator("canvas.maplibregl-canvas").waitFor();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: fileURLToPath(new URL("identity-mobile.png", OUT)), fullPage: true });
});
