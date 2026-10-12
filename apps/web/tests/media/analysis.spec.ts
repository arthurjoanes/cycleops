import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";

const OUT = new URL("../../../../docs/media/", import.meta.url);

for (const capture of [
  { file: "analysis-summary.png", width: 1440, height: 1000, tab: "Resumo da gravação" },
  { file: "analysis-quality.png", width: 1440, height: 1000, tab: "Qualidade do feed" },
  { file: "analysis-mobile.png", width: 320, height: 800, tab: "Resumo da gravação" },
]) {
  test(`analysis: ${capture.file}`, async ({ page }) => {
    await page.setViewportSize({ width: capture.width, height: capture.height });
    await page.goto("./#/?mode=recorded");
    await page.getByRole("button", { name: "Pausar", exact: true }).click();
    await page.getByRole("button", { name: "Ir para o incidente", exact: true }).click();
    if (capture.width < 700) await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "Analisar dados", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Análise dos dados", exact: true });
    await dialog.getByRole("button", { name: capture.tab, exact: true }).click();
    await expect(dialog.getByRole("heading", { name: capture.tab, exact: true })).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: fileURLToPath(new URL(capture.file, OUT)) });
  });
}
