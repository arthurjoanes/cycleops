import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";

const out = new URL("../../../../docs/media/", import.meta.url);
const paused = "#/?mode=recorded&artifact=48e2c7d55854510f1845b9ef2950aa0274a7c0f4363f090a71032f3b0eed0031&at=1126.445";

for (const [name, width] of [["desktop", 1440], ["tablet", 768], ["mobile", 320]] as const) {
  test(`uiux: ${name}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`./${paused}`);
    await expect(page.getByRole("button", { name: "Continuar", exact: true })).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    if (width >= 768) {
      await page.locator("canvas.maplibregl-canvas").waitFor();
      await page.waitForTimeout(800);
    }
    await page.screenshot({ path: fileURLToPath(new URL(`uiux-${name}.png`, out)) });
  });
}

test("uiux: analysis", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`./${paused}`);
  await page.getByRole("button", { name: "Analisar dados", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Resumo da gravação", exact: true })).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: fileURLToPath(new URL("uiux-analysis.png", out)) });
});
