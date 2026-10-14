import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const paused = "#/?mode=recorded&artifact=48e2c7d55854510f1845b9ef2950aa0274a7c0f4363f090a71032f3b0eed0031&at=1126.445";

test("time zone selector keeps the exact paused replay and initial focus is not stolen", async ({ page }) => {
  await page.goto(`./${paused}`);
  await expect(page.getByRole("button", { name: "Continuar", exact: true })).toBeVisible();
  await expect(page.locator("#queue-title")).not.toBeFocused();
  const zone = page.getByRole("combobox", { name: "Fuso dos horários", exact: true });
  await zone.focus();
  await zone.selectOption("local");
  await expect(zone).toBeFocused();
  await expect(page.getByRole("slider", { name: "Linha do tempo" })).toHaveValue("1126.445");
  await page.getByTitle("Detalhes do replay", { exact: true }).click();
  await expect(page.getByText(/^Evento \d+ de \d+/)).toContainText("Evento 69 de 218");
  await page.getByTitle("Detalhes do replay", { exact: true }).click();
  expect(new URLSearchParams(new URL(page.url()).hash.slice(3)).get("zone")).toBe("local");
  await zone.selectOption("oslo");
  await expect(zone).toHaveValue("oslo");
});

test("workspace remains accessible on desktop and narrow mobile", async ({ page }) => {
  for (const width of [1440, 768, 320]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`./${paused}`);
    await expect(page.getByRole("button", { name: "Continuar", exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
    const indicators = page.getByRole("list", { name: "Indicadores", exact: true });
    const positions = await Promise.all(["24", "12", "0", "100%"].map((value) => indicators.getByText(value, { exact: true }).boundingBox()));
    const tops = positions.map((box) => {
      if (!box) throw new Error("Indicator has no visible bounding box");
      return box.y;
    });
    expect(Math.max(...tops) - Math.min(...tops)).toBeLessThan(2);
    if (width < 768) {
      for (const name of ["Continuar", "Passo", "Copiar este momento", "Ir para o incidente", "Analisar dados"]) {
        const button = page.getByRole("button", { name, exact: true });
        expect((await button.boundingBox())?.height).toBeGreaterThanOrEqual(44);
      }
    }
    const findings = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
    expect(findings.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([]);
  }
});

for (const width of [1440, 768, 320]) {
  test(`full map legend stays inside the map at ${width}px with station details`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`./${paused}&station=537`);
    if (width < 768) {
      await page.getByRole("button", { name: "Fechar", exact: true }).click();
      await page.getByRole("button", { name: "Mapa", exact: true }).click();
    }
    await expect(page.getByRole("button", { name: "Continuar", exact: true })).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    await page.locator("canvas.maplibregl-canvas").waitFor();
    const map = page.locator("section[aria-label='Mapa']");
    const toggle = map.getByText("Legenda", { exact: true });
    await toggle.click();
    await expect(toggle.locator("..")).toHaveJSProperty("open", true);
    await expect(map.getByRole("list", { name: "Legenda do mapa", exact: true })).toBeVisible();
    const legend = await map.getByRole("list", { name: "Legenda do mapa", exact: true }).boundingBox();
    const area = await map.boundingBox();
    if (!legend || !area) throw new Error("Map and legend must have visible bounding boxes");
    expect(legend.x).toBeGreaterThanOrEqual(area.x);
    expect(legend.x + legend.width).toBeLessThanOrEqual(area.x + area.width);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
  });
}

test("closing station details opened from a ranking restores the analysis action", async ({ page }) => {
  for (const width of [1440, 320]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`./${paused}`);
    await page.getByRole("button", { name: "Analisar dados", exact: true }).click();
    const analysis = page.getByRole("dialog", { name: "Análise dos dados", exact: true });
    await analysis.getByRole("region", { name: "Tempo sem bicicletas", exact: true }).getByRole("button").first().click();
    await expect(analysis).toBeHidden();
    await page.getByRole("button", { name: "Fechar", exact: true }).click();
    await expect(page.getByRole("button", { name: "Analisar dados", exact: true })).toBeFocused();
  }
});

test("session information opens on demand and Escape restores its trigger", async ({ page }) => {
  await page.goto(`./${paused}`);
  await expect(page.getByRole("button", { name: "Continuar", exact: true })).toBeVisible();
  await expect(page.getByTestId("mode")).toBeVisible();
  await expect(page.getByTestId("publication")).toBeHidden();
  const trigger = page.getByTitle("Dados da sessão", { exact: true });
  await trigger.click();
  await expect(page.getByTestId("publication")).toBeVisible();
  await expect(page.getByTestId("connection")).toContainText("arquivo gravado em");
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("publication")).toBeHidden();
  await expect(trigger).toBeFocused();
});

for (const width of [320, 390]) {
  test(`view buttons expose their state and work with the keyboard at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`./${paused}`);
    const views = page.getByRole("group", { name: "Visão", exact: true });
    const list = views.getByRole("button", { name: "Lista", exact: true });
    const map = views.getByRole("button", { name: "Mapa", exact: true });
    await expect(list).toHaveAttribute("aria-pressed", "true");
    await list.focus();
    await page.keyboard.press("Tab");
    await expect(map).toBeFocused();
    await expect(map).toHaveCSS("outline-style", "solid");
    await page.keyboard.press("Enter");
    await expect(map).toHaveAttribute("aria-pressed", "true");
    await expect(list).toHaveAttribute("aria-pressed", "false");
    await page.keyboard.press("Shift+Tab");
    await expect(list).toBeFocused();
    await page.keyboard.press("Space");
    await expect(list).toHaveAttribute("aria-pressed", "true");
    await expect(map).toHaveAttribute("aria-pressed", "false");
  });
}
