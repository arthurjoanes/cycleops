import { expect, test } from "@playwright/test";
import { converge, instrument, openStream } from "./stack";

test.beforeAll(async () => { await converge(); });
test.beforeEach(async ({ context }) => { await instrument(context); });

test("server analysis reads diagnostics through Caddy and keeps stream context", async ({ page, request }) => {
  await openStream(page);
  await page.getByRole("button", { name: "Analisar dados", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Análise dos dados", exact: true });
  await expect(dialog.getByRole("heading", { name: "Evolução dos indicadores", exact: true })).toBeVisible();
  const responsePromise = page.waitForResponse((response) => response.url().endsWith("/api/v1/feed-diagnostics"));
  await dialog.getByRole("button", { name: "Qualidade do feed", exact: true }).click();
  const response = await responsePromise;
  expect(response.status()).toBe(200);
  const diagnostics = await response.json();
  const snapshot = await (await request.get("api/v1/stations/snapshot")).json();
  expect(diagnostics.streamId).toBe(snapshot.streamId);
  expect(diagnostics.mode).toBe("synthetic");
  expect(diagnostics.totals.attempts).toBeGreaterThan(0);
  await expect(dialog.getByRole("table", { name: "Coleta por feed no período" })).toBeVisible();
  await expect(dialog.getByText("Dados de simulação.", { exact: false })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Resumo da gravação", exact: true })).toHaveCount(0);
  await dialog.getByRole("button", { name: "Fechar análise", exact: true }).click();
  await expect(page.getByTestId("connection")).toHaveText("conectada");
});
