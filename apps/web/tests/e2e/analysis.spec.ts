import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";

async function openAnalysis(page: Page) {
  await page.goto("./#/?mode=recorded");
  await page.getByRole("button", { name: "Pausar", exact: true }).click();
  await page.getByRole("button", { name: "Analisar dados", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Análise dos dados", exact: true });
  await expect(dialog.getByRole("heading", { name: "Resumo da gravação", exact: true })).toBeVisible();
  return dialog;
}

test("whole recording summary, observed rankings and station navigation", async ({ page }) => {
  const dialog = await openAnalysis(page);
  await expect(dialog.locator("dl > div").filter({ has: page.locator("dt", { hasText: /^Incidentes confirmados$/ }) }).locator("dd")).toHaveText("40");
  await expect(dialog.locator("dl > div").filter({ has: page.locator("dt", { hasText: /^Incidentes resolvidos$/ }) }).locator("dd")).toHaveText("2");
  const ranking = dialog.getByRole("region", { name: "Tempo sem bicicletas", exact: true });
  await expect(ranking.getByRole("columnheader", { name: "% observado", exact: true })).toBeVisible();
  const station = ranking.getByRole("button").first();
  const name = await station.innerText();
  await station.click();
  await expect(dialog).toBeHidden();
  await expect(page.locator("#details-title")).toContainText(name);
  await expect(page.locator("#details-title")).toBeFocused();
});

test("quality distinguishes HTTP from accepted publications without contacting the API", async ({ page }) => {
  const api: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.includes("/api/")) api.push(request.url());
  });
  const dialog = await openAnalysis(page);
  await dialog.getByRole("button", { name: "Qualidade do feed", exact: true }).click();
  const metric = (name: string) => dialog.locator("dl > div").filter({ has: page.locator("dt", { hasText: new RegExp(`^${name}$`) }) }).locator("dd");
  await expect(metric("Tentativas")).toHaveText("249");
  await expect(metric("HTTP 200 ou 304")).toHaveText("249 de 249 respostas");
  await expect(metric("Publicações válidas")).toHaveText("225");
  await expect(metric("Publicações rejeitadas")).toHaveText("24");
  await expect(dialog.getByRole("table", { name: "Coleta por feed no período" })).toBeVisible();
  await dialog.getByText("Disponibilidade: resultados e últimas tentativas", { exact: true }).click();
  await expect(dialog.getByText("Publicação repetida", { exact: true }).first()).toBeVisible();
  expect(api).toEqual([]);
});

test("final-minute filters recut observations without seeking the replay", async ({page}) => {
  const dialog=await openAnalysis(page);
  const position=await page.locator('#replay-time').inputValue();
  const period=dialog.getByRole('combobox',{name:'Período observado'});
  await period.selectOption('last15');
  const metric=(name:string)=>dialog.locator('dl > div').filter({has:page.locator('dt',{hasText:new RegExp(`^${name}$`)})}).locator('dd');
  await expect(metric('Duração')).toHaveText('15 min');
  await expect(page.locator('#replay-time')).toHaveValue(position);
  await expect(dialog.getByRole('img',{name:/Tempo da fonte:/})).toBeVisible();
  await period.selectOption('15');
  await expect(metric('Duração')).toHaveText('15 min');
  await expect(metric('Confirmações no recorte')).not.toHaveText('0');
  await period.selectOption('30');
  await expect(metric('Duração')).toHaveText('30 min');
  await period.selectOption('all');
  await expect(metric('Incidentes confirmados')).toHaveText('40');
  await expect(metric('Incidentes resolvidos')).toHaveText('2');
});

test("city trends have an accessible table and analysis fits at 320 px", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 800 });
  const dialog = await openAnalysis(page);
  await dialog.getByRole("button", { name: "Evolução da cidade", exact: true }).click();
  await expect(dialog.getByRole("heading", { name: "Evolução dos indicadores", exact: true })).toBeVisible();
  await dialog.getByText(/^Tabela dos indicadores \(/).click();
  await expect(dialog.getByRole("region", { name: "Valores dos indicadores", exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "Cobertura válida", exact: true }).click();
  await expect(dialog.getByRole("img", { name: /^Cobertura válida em percentual/ })).toBeVisible();
  for (const tab of ["Resumo da gravação", "Evolução da cidade", "Qualidade do feed"]) {
    await dialog.getByRole("button", { name: tab, exact: true }).click();
    const results = await new AxeBuilder({ page }).include('[role="dialog"]').withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
    expect(results.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
    const box = await dialog.boundingBox();
    expect(box?.width).toBeLessThanOrEqual(320);
  }
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("button", { name: "Analisar dados", exact: true })).toBeFocused();
});
