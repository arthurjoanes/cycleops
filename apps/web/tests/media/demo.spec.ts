import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import { compose, connection } from "../compose/stack";

// docs/media/demo.webm: the Compose stack in the simulation, about 30 s. By hand, from the
// repository root, with a fresh stack (the worker scripts the incident on S-0001 from 2 min
// after the stream starts, so an old volume never shows it):
//   CYCLEOPS_NET=10.89.2 docker compose -p cycleops-e2e -f compose.yaml -f compose.offline.yaml up -d --build --wait
//   cd apps/web && CYCLEOPS_DEMO=1 corepack pnpm exec playwright test --project=media demo
//   CYCLEOPS_NET=10.89.2 docker compose -p cycleops-e2e -f compose.yaml -f compose.offline.yaml down -v
const OUT = fileURLToPath(new URL("../../../../docs/media/demo.webm", import.meta.url));
const EDGE = "http://127.0.0.1:8080/";
const SIZE = { width: 1280, height: 720 };

test.skip(!process.env.CYCLEOPS_DEMO, "needs the Compose stack; set CYCLEOPS_DEMO=1");
test.use({ baseURL: EDGE });

interface Snapshot {
  stations: { decision: { stationId: string; incidents: { state: string }[] } }[];
}

test("demo: stream, confirmed incident, reconnection, file replay", async ({ browser, request }, testInfo) => {
  test.setTimeout(600_000);
  // All the waiting happens before the recorded page exists.
  await expect
    .poll(
      async () => {
        const response = await request.get("api/v1/stations/snapshot");
        if (!response.ok()) return false;
        const { stations } = (await response.json()) as Snapshot;
        return stations.some(
          (s) => s.decision.stationId === "S-0001" && s.decision.incidents.some((i) => i.state === "confirmed"),
        );
      },
      // The API allows 60 REST requests per minute per address.
      { intervals: [3_000], timeout: 480_000 },
    )
    .toBe(true);

  const context = await browser.newContext({
    baseURL: EDGE,
    viewport: SIZE,
    recordVideo: { dir: testInfo.outputPath("video"), size: SIZE },
  });
  const page = await context.newPage();
  // Pauses only so a person can read the screen.
  const hold = (ms: number) => page.waitForTimeout(ms);

  await page.goto("./#/?mode=stream");
  await expect(page.getByTestId("mode")).toContainText("Simulação");
  await expect(connection(page)).toHaveText("conectada", { timeout: 60_000 });
  const top = page.getByRole("list", { name: "Estações em prioridade" }).locator("li").first();
  await expect(top).toContainText("confirmado");
  await hold(4_000);

  await top.getByRole("button").click();
  await expect(page.getByText("Motivo da prioridade")).toBeVisible();
  await hold(5_000);

  // The api stops (stream_closed), the page reconnects once it is back.
  const restart = compose(["restart", "api"]);
  await expect(connection(page)).not.toHaveText("conectada", { timeout: 30_000 });
  await expect(connection(page)).toHaveText("conectada", { timeout: 60_000 });
  await restart;
  await hold(3_000);

  await page.getByRole("button", { name: "Simulação (arquivo)" }).click();
  await expect(connection(page)).toHaveText("arquivo local, seed 42");
  await expect(page.getByText(/^Evento \d+ de \d+/)).toBeVisible();
  await hold(4_000);
  await page.getByRole("button", { name: "Pausar" }).click();
  await expect(page.getByRole("button", { name: "Continuar" })).toBeVisible();
  await hold(3_000);

  await context.close();
  await page.video()?.saveAs(OUT);
});
