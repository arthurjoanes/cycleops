import { fileURLToPath } from "node:url";
import { type Page, test } from "@playwright/test";

// Captures of the real interface for docs/media (synthetic mode, mode visible in the header).
// By hand: corepack pnpm exec playwright test --project=media
const OUT = new URL("../../../../docs/media/", import.meta.url);

async function open(page: Page) {
  await page.goto("./#/?mode=synthetic");
  await page.getByText(/^Evento \d+ de \d+/).waitFor({ timeout: 60_000 });
  // Pause first: on narrow screens the drawer hides the controls from the accessibility tree.
  await page.getByRole("button", { name: "Pausar" }).click();
  await page.getByRole("button", { name: "Ir para o incidente" }).click();
}

const shot = (page: Page, name: string) =>
  page.waitForTimeout(2_500).then(() => page.screenshot({ path: fileURLToPath(new URL(name, OUT)) }));

test("1440: incident confirmed, details over the map", async ({ page }) => {
  await open(page);
  await shot(page, "desktop-1440.png");
});

test("768: queue and map", async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 1024 });
  await open(page);
  await page.getByRole("button", { name: "Fechar" }).click();
  await shot(page, "tablet-768.png");
});

test("320: details in the drawer", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 640 });
  await open(page);
  await shot(page, "mobile-320-detalhes.png");
});
