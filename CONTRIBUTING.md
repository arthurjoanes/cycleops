# Development guide

CycleOps lists Oslo Bysykkel stations with no bikes or no docks, in priority order, from the open GBFS feed. Read-only, no login. Two ways to run: the static replay (GitHub Pages or `apps/web`) and the Compose stack (PostgreSQL, worker, API with SSE, Caddy) with the simulation or the live feed.

## Layout

- `apps/backend/`: Python package `cycleops` (uv, Ruff, mypy, pytest).
  - `collection/`: HTTP client, attempt classification, breaker and quarantine, polling loop, recording format.
  - `adapters/gbfs.py`: GBFS 2.3 validation and normalization. `adapters/synthetic.py`: seeded synthetic source and its scenarios.
  - `domain/`: the engine. `step(state, input, policy) -> (state, events)`, pure, no I/O, no wall clock.
  - `application/replay.py`: compile-replay (recording or synthetic, through the engine, to the replay artifact).
  - `application/ingest.py`: one worker entry as one transaction (state, `event_log`, `feed_publications`, `pg_notify`).
  - `adapters/postgres/`: tables, connections and the advisory lock, StreamState load/save, read queries for the API (snapshot, history, feed-health, events), purge.
  - `worker/`: `python -m cycleops.worker`. Source (live collector or synthetic on the wall clock), processor, ticker, purge and quota, raw archive, watchdog.
  - `api/`: `python -m cycleops.api`. FastAPI read-only routes, SSE stream, tailer (LISTEN + 1 s poll) and per-client queues.
  - `contracts.py`: Pydantic models of everything sent to the browser. Source of `packages/contracts/`.
  - `policy.py`: Pydantic models for the files in `policy/`, and `policyVersion`.
  - `cli/`: `inspect-feed`, `record-feed`, `compile-replay`, `generate-artifacts`, `migrate`.
  - `migrations/`: Alembic. `migrate` creates the roles as superuser, then upgrades as `cycleops_migrator`.
- `apps/web/`: React, TypeScript strict, Vite, Zustand, MapLibre (corepack pnpm, Node 24).
  - `src/features/stations/store.ts`: the store and `applyEvents` (contiguous sequences). `src/streams/`: `ReplayPlayer`, `LiveSseSource` and `api.ts` (snapshot, history, feed-health), artifact loading, the seeded load generator (`#/?source=load`). `src/app/useDataSession.ts` picks the source: server stream first in the edge build, artifacts only on Pages.
  - `src/features/priorities/`: queue order, freeze, indicators. `src/features/stations/`: list, details, step chart, map.
  - `src/app/`: layout, header, screen states, hash routing.
  - `tests/unit/` (Vitest), `tests/e2e/` (Playwright against the Pages build), `tests/compose/` (Playwright against the Compose stack, `playwright.compose.config.ts`), `tests/performance/` and `tests/media/` (by hand).
  - `scripts/`: `generate-contracts.mjs`, `serve-pages.mjs` (static server that behaves like Pages), `check-bundle.mjs`.
- `scripts/basemap/`: generates the map base from OpenStreetMap. Run by hand only.
- `scripts/smoke`: health, snapshot and one SSE event through the edge on 127.0.0.1:8080. `scripts/checks`: the local equivalent of CI.
- `infra/`: `backend.Dockerfile` (migrate, worker, api), `edge.Dockerfile` (web build + Caddy), `caddy/Caddyfile`. `compose.yaml`, `compose.offline.yaml` (no egress), `compose.test.yaml` (only PostgreSQL for the integration tests).
- `policy/`: `priority-policy.yaml`, `provider-oslo.yaml`, `provider-synthetic.yaml`. Literal values only.
- `fixtures/recorded/`: recordings of the real feed (NDJSON.gz with a manifest) and the NLOD `NOTICE`. A `demo.yaml` there names the recording that becomes the recorded replay; it exists only once a recording passes acceptance.
- `fixtures/synthetic/demo.yaml`: parameters of the synthetic demo artifact.
- `data/recordings/`: full recordings, not versioned.
- `docs/architecture.md` (processes, connections and the test covering each), `docs/adr/` (decisions; new ones start as Proposto), `docs/results/` (every run with commit and command, indexed in `index.md`; `a11y-manual.md` is filled by a person; `publicacao.md` lists what only Arthur can do), `docs/media/` (captures, made with the Playwright `media` project; `tests/media/demo.spec.ts` records `demo.webm` against the Compose stack with `CYCLEOPS_DEMO=1`).

## Commands

Run from the repository root. If `uv` is not on PATH, use `python -m uv`.

```bash
uv sync --project apps/backend
uv run --project apps/backend pytest apps/backend
uv run --project apps/backend ruff check apps/backend
uv run --project apps/backend ruff format --check apps/backend
uv run --project apps/backend mypy --config-file apps/backend/pyproject.toml apps/backend/src apps/backend/tests
```

Feed collection (calls the real feed; needs the identifier):

```bash
export CYCLEOPS_CLIENT_IDENTIFIER=<your-name>-cycleops
uv run --project apps/backend inspect-feed --deadline-seconds 30
uv run --project apps/backend record-feed --minutes 10 --deadline-seconds 30 --out fixtures/recorded/sample-<UTC>.ndjson.gz
uv run --project apps/backend inspect-feed --recording fixtures/recorded/<file>.ndjson.gz
```

`inspect-feed` makes exactly 5 requests. `inspect-feed --recording` makes none. Timestamps in file names use `YYYYMMDDTHHMMSSZ`.

Replay and contracts (no network):

```bash
uv run --project apps/backend generate-artifacts          # rewrites the generated zones
uv run --project apps/backend generate-artifacts --check  # exit 1 if they are out of date
uv run --project apps/backend compile-replay --recording <file>.ndjson.gz --out <dir>
uv run --project apps/backend compile-replay --synthetic fixtures/synthetic/demo.yaml --out <dir>
```

`compile-replay --recording` writes only when the recording passes acceptance (an incident goes from confirmed to resolved) and exits 3 otherwise.

Web, from `apps/web`:

```bash
corepack pnpm install
corepack pnpm dev                 # http://localhost:5173
corepack pnpm lint
corepack pnpm typecheck
corepack pnpm test
corepack pnpm e2e                 # builds for Pages and runs Playwright under /cycleops/
corepack pnpm build:pages && corepack pnpm check:bundle
corepack pnpm preview:pages       # http://127.0.0.1:4173/cycleops/
corepack pnpm generate:contracts  # types.ts and validators from packages/contracts/schemas
corepack pnpm perf                # frontend load and lab Web Vitals, writes docs/results/data/
corepack pnpm exec playwright test --project=media  # captures in docs/media/
```

`e2e`, `perf` and `media` reuse a server already on port 4173; stop `preview:pages` first or the run tests the old `dist/`.

Against the Compose stack (port 8080, project `cycleops-e2e`; the last test resets that project's database with `down -v`):

```bash
CYCLEOPS_NET=10.89.2 docker compose -p cycleops-e2e -f compose.yaml -f compose.offline.yaml up -d --build --wait
corepack pnpm e2e:compose                                   # in apps/web
CYCLEOPS_MEASURE=1 corepack pnpm e2e:compose latency        # 10 min receive-to-paint and commit-to-write
CYCLEOPS_DEMO=1 corepack pnpm exec playwright test --project=media demo   # records docs/media/demo.webm on a fresh stack
```

Compose (Docker only):

```bash
docker compose up --build                                  # simulation on http://127.0.0.1:8080
docker compose -p cycleops-live --env-file .env.live up --build   # live feed; .env.live from .env.live.example
scripts/smoke                                              # with a stack up
scripts/checks                                             # everything CI runs, locally
```

Integration tests (real PostgreSQL):

```bash
docker compose -p cycleops-test -f compose.test.yaml up -d --wait
export CYCLEOPS_TEST_DATABASE_URL=postgresql://postgres:local-only-test@127.0.0.1:55432/postgres
uv run --project apps/backend pytest apps/backend
docker compose -p cycleops-test -f compose.test.yaml down -v
```

Without `CYCLEOPS_TEST_DATABASE_URL` the tests marked `integration` are skipped. On Windows, async psycopg needs the selector loop: run coroutines through `cycleops.aio.run`.

Text check for the pt-BR Markdown (must print nothing):

```bash
git ls-files -z -- '*.md' ':!README.en.md' ':!CONTRIBUTING.md' ':!THIRD_PARTY_NOTICES.md' | xargs -0 -r grep -n -i -E '\bevid(e|ê)nc|\bensaio|\batest|\bassegur|\bprov(a|as)\b|\bcomprov|\brobust'
```

## Rules

- The real feed is called only by the collection CLIs and the worker, through `cycleops.collection`. With `DATABASE_URL` set, the CLIs take the collector's advisory lock and refuse to run while a worker leads; with the live stack up, recordings come from the raw archive (`record-feed --from-archive`). Every request sends `Client-Identifier` from `CYCLEOPS_CLIENT_IDENTIFIER`; there is no default in code. One request in flight per feed, TTL cadence, no load tests and no fault injection against the operator.
- Tests never reach the external network: an autouse fixture in `apps/backend/tests/conftest.py` blocks real HTTPX transports outside loopback. Use `httpx.MockTransport`.
- Do not fabricate results, bypass a check, weaken or delete a test to make it pass, add a dependency without a reason, or present synthetic data as real. Synthetic payload files carry `synthetic` in the name.
- Performance numbers only with a run recorded in `docs/results/`.
- No personal data, tokens or users in recordings.
- Scope changes and new infrastructure need an accepted ADR. A new architecture decision goes to `docs/adr/` as Proposto; Arthur accepts it. Numeric collection values may change in `policy/` with the data written in ADR 0002; semantic changes stay a proposal until accepted.
- Generated zones, never edited by hand: `packages/contracts/`, `apps/web/public/replay/`, `apps/web/public/basemap/`. After changing the engine, the policy, the adapter or `contracts.py`, run `generate-artifacts` and then `corepack pnpm generate:contracts`, and commit the result; `--check`, a test and the CI diff fail otherwise. The basemap changes only through `scripts/basemap/generate.sh`.
- The worker is the only writer. The API connects as `cycleops_api` (SELECT only) and never holds a database session during an SSE stream. Every event goes to `event_log` and `pg_notify` in the same transaction as the state.
- The web never recomputes domain rules: it applies the engine's events in sequence order and shows them. Text from the feed is always text (no `innerHTML`, no `dangerouslySetInnerHTML`; lint enforces it). CSP exceptions are listed in `SECURITY.md`; `script-src` gets none.
- Code: the domain does not import FastAPI, SQLAlchemy, HTTPX or React. No `helpers.py`, `utils.ts`, `Manager` or catch-all modules. Interfaces only at replaceable boundaries (source, Clock, repository, event delivery). Comments explain decisions and invariants. Generated text files are UTF-8 with `\n`.
- GBFS input models use `extra="ignore"`, never `forbid`: Oslo sends `num_vehicles_available`.

## Text

Versioned text in pt-BR is short and plain, like a developer writing for their own use. Avoid: the words caught by the text check above; decorative adjectives (robusto, completo, eficiente); slogans; defensive disclaimers; lists of three for rhythm; sentences that end in an "X, não Y" contrast; titles ending in a period; bold mid-paragraph; dramatic dashes; uppercase labels above titles; 01/02/03 menus; hero sections, emoji, decorative badge bars; the word "LATER" (write "depois do MVP"). The READMEs use five local SVG navigation links (blue demo, purple architecture, green run, ochre results, LinkedIn blue) and a verified CI badge, following the author's portfolio format. Keep local Devicon stack icons at 64 × 64 px, the LinkedIn contact icon at 24 × 24 px, their source comments and MIT license. The stack table explains reasons and trade-offs; architecture links to implementation, and dependency links point to lockfiles. Keep Portuguese and English aligned. Brand assets, colors and contrast are documented in `docs/brand/README.md`. Technical terms stay in English when that is the norm. Never invent numbers, clients or experience.
