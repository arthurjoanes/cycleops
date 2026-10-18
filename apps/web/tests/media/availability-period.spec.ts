import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";

const out = new URL("../../../../docs/media/", import.meta.url);
const paused = "#/?mode=recorded&artifact=48e2c7d55854510f1845b9ef2950aa0274a7c0f4363f090a71032f3b0eed0031&at=1741.76";

for (const [name, width, height, period] of [["desktop", 1440, 1050, "30"], ["mobile", 390, 844, "15"]] as const) {
  test(`availability period: ${name}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto(`./${paused}`, { waitUntil: "networkidle" });
    await expect(page.getByRole("button", { name: "Continuar", exact: true })).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: fileURLToPath(new URL(`availability-${name}.png`, out)) });
    await page.getByRole("button", { name: "Analisar dados", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Análise dos dados", exact: true });
    await dialog.getByRole("combobox", { name: "Período observado" }).selectOption(period);
    await expect(dialog.getByRole("img", { name: /^Tempo da fonte:/ })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: fileURLToPath(new URL(`availability-analysis-${name}.png`, out)) });
  });
}
