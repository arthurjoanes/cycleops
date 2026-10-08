# CycleOps

Lists Oslo Bysykkel stations with no bikes or no free docks, in priority order, with the reason and the data behind each decision. The data comes from the operator's open GBFS feed. The main README is in Portuguese (`README.md`).

![CycleOps in simulation mode: priority queue on the left, map of Oslo with the stations on the right, and the details of an empty station open over the map](docs/media/desktop-1440.png)

A 27 s video, [docs/media/demo.webm](docs/media/demo.webm), shows the simulation over the server stream in Compose: a confirmed incident at the top of the queue with its reason in the details, the API restarting (connection "reconectando", then "conectada" again) and the switch to the file replay, paused.

## Run

Docker only. From the repository root:

```bash
docker compose up --build
```

Open http://127.0.0.1:8080. It starts in simulation mode: a worker generates seeded stations and runs them through the same engine as the real feed, the API streams the events over SSE and Caddy serves the interface. The database is PostgreSQL with no published port. The default passwords in `compose.yaml` are insecure on purpose and only for local use; `.env.example` shows how to override them.

For the real Oslo feed, copy `.env.live.example` to `.env.live`, set your identifier as `<organization>-<application>` and use another project name, which keeps a separate database:

```bash
docker compose -p cycleops-live --env-file .env.live up --build
```

After changing the code, start with `--build`: the images have a fixed local tag and a plain `up` reuses the old build. Only one stack publishes port 8080 at a time; stop the other before switching: `docker compose down` for the simulation, `docker compose -p cycleops-live down` for live.

The web app alone needs Node 24 with corepack:

```bash
cd apps/web
corepack pnpm install
corepack pnpm dev
```

The dev server (http://localhost:5173) tries the server stream first, through a proxy to the Compose edge at 127.0.0.1:8080; without the stack up it shows a load error with a button to open the file replay. The GitHub Pages build has the replay only, served under `/cycleops/`:

```bash
corepack pnpm build:pages
corepack pnpm preview:pages
```

Open http://127.0.0.1:4173/cycleops/.

## Test

What CI runs except gitleaks, locally (Docker, git, uv, Node 24 with corepack and Playwright's Chromium):

```bash
scripts/checks
```

Commands for each part are in `CONTRIBUTING.md`; the E2E against Compose runs from `apps/web` with `corepack pnpm e2e:compose`.

## Data modes

- Live: the worker polls the Oslo feed every 15 s and the API streams the events over SSE. Local Compose only.
- Simulation: seeded stations and counts, with a permanent "Simulação" stripe. In Compose it comes from the worker, through the same path as live; on Pages and under "Simulação (arquivo)", from an artifact generated offline.
- Recorded replay: a real recording of the feed run through the engine and played in the browser. It exists only once a recording has an incident that is confirmed and then resolved; there is none yet.

The mode is always in the header, with the seed in simulation and the connection state. When the source is down, the screen says so and offers the file replay; it never switches by itself. The replay has pause, 1×, 5×, 20×, step and "go to the incident".

## Limitations

- Not an official Oslo Bysykkel service.
- Read-only: no booking, no dispatch.
- The feed has no SLA.
- Live runs only in the local Compose stack.
- In Compose over HTTP/1.1 the browser opens at most 6 connections per origin, and each tab holds one for SSE. The API also allows 4 streams per IP: a fifth tab closes the oldest.
- The map base is simplified and not for navigation.
- The CSP on Pages is a meta tag and weaker than Caddy's header (see `SECURITY.md`).

## Sources and licenses

- Oslo Bysykkel data under NLOD 2.0 and the OpenStreetMap map base under ODbL: [DATA_SOURCES.md](DATA_SOURCES.md).
- Third-party software: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
- Code under the MIT license: [LICENSE](LICENSE).

## Architecture

Processes, connections and the test that covers each one in [docs/architecture.md](docs/architecture.md). Decisions in [docs/adr/](docs/adr/). Run results, with commit and command, in [docs/results/](docs/results/). These documents are in Portuguese.
