# Resultados

Cada execução registrada no repositório, com o commit do código usado. Comandos rodam da raiz; `uv` pode ser `python -m uv`.

| Resultado | Arquivo | Comando | Commit | Data (UTC) |
|---|---|---|---|---|
| Coleta única dos 5 feeds | `fixtures/recorded/inspect-20261007T032439Z.ndjson.gz` | `CYCLEOPS_CLIENT_IDENTIFIER=arthurjoanes-cycleops uv run --project apps/backend inspect-feed --deadline-seconds 30` | 0da0d2c (rodou antes do commit, com o mesmo cliente) | 2026-10-07 03:24 |
| Amostra de 10 min | `fixtures/recorded/sample-20261007T032452Z.ndjson.gz` | `CYCLEOPS_CLIENT_IDENTIFIER=arthurjoanes-cycleops uv run --project apps/backend record-feed --minutes 10 --deadline-seconds 30 --out fixtures/recorded/sample-20261007T032452Z.ndjson.gz` | 0da0d2c (idem) | 2026-10-07 03:24 a 03:34 |
| Validação do feed | `docs/results/feed-validation.md` | `uv run --project apps/backend inspect-feed --recording fixtures/recorded/inspect-20261007T032439Z.ndjson.gz fixtures/recorded/sample-20261007T032452Z.ndjson.gz` | 98717c1 | 2026-10-07 |
| Testes Python, ruff e mypy: 130 testes passando, sem rede | | `uv run --project apps/backend pytest apps/backend`, `ruff check apps/backend`, `mypy --config-file apps/backend/pyproject.toml apps/backend/src apps/backend/tests` | 98717c1 | 2026-10-07 |
| Artefato sintético da demo: 242 eventos, 85 KB em gzip, fechamento à 01:00 e incidentes resolvidos | `apps/web/public/replay/synthetic/` | `uv run --project apps/backend generate-artifacts` | c5845b1 | 2026-10-08 |
| Aceite da amostra gravada de 10 min: não passa (40 incidentes confirmados, nenhum resolvido), saída 3 | `fixtures/recorded/sample-20261007T032452Z.ndjson.gz` | `uv run --project apps/backend compile-replay --recording fixtures/recorded/sample-20261007T032452Z.ndjson.gz --out data/replay-check` | c5845b1 | 2026-10-08 |
| Testes Python, ruff e mypy da fase 1: 215 testes passando, sem rede; `generate-artifacts --check` sem diferença | | `uv run --project apps/backend pytest apps/backend`, `generate-artifacts --check` | c5845b1 | 2026-10-08 |
| Base do mapa: água, costa, parques e vias principais do OpenStreetMap, 1,9 MB | `apps/web/public/basemap/` | `scripts/basemap/generate.sh` | 5794b7a (rodou antes do commit) | 2026-10-08 01:30 |
| Testes Python, ruff e mypy na fase 2: 215 testes passando; `generate-artifacts --check` sem diferença | | os mesmos da fase 1 | 5794b7a (rodou antes do commit; o backend não mudou) | 2026-10-08 |
| Testes da web: ESLint e tsc sem erro, 30 testes no Vitest, 12 E2E no Playwright contra o build do Pages sob `/cycleops/` | | em `apps/web`: `corepack pnpm lint`, `typecheck`, `test`, `e2e` | 5794b7a | 2026-10-08 |
| Orçamento do bundle: 116.775 bytes em gzip, teto de 127.026 | `apps/web/bundle-budget.json` | em `apps/web`: `corepack pnpm build:pages && corepack pnpm check:bundle` | 5794b7a | 2026-10-08 |
| Teste de carga do frontend: recebimento até pintura com p95 de 157 a 232 ms; gatilho de virtualização atingido em "Todas as estações" com 1.000 estações | `docs/results/performance.md`, `docs/results/data/frontend-load.json` | em `apps/web`: `corepack pnpm perf` | 5794b7a | 2026-10-08 02:24 |
| Web Vitals de laboratório, mediana de 3: LCP 3.060 ms, CLS 0,069, INP 176 ms | `docs/results/performance.md`, `docs/results/data/web-vitals.json` | o mesmo `corepack pnpm perf` | 5794b7a | 2026-10-08 02:25 |
| Experimento revertido: `content-visibility: auto` em "Todas as estações", 1.000 estações | `docs/results/data/frontend-load-content-visibility.json` | em `apps/web`: `corepack pnpm perf -g "1.000.*view all"` | 5794b7a com a mudança local | 2026-10-08 |
| gitleaks 8.30.1 no histórico: 9 commits, nenhum vazamento | | imagem do `ci.yml` com `GIT_CONFIG_*` de `safe.directory`, `git --redact` | 5794b7a | 2026-10-08 02:33 |
| Capturas da interface no modo Simulação, 1440, 768 e 320 px | `docs/media/` | em `apps/web`: `corepack pnpm exec playwright test --project=media` | 5794b7a | 2026-10-08 |
| Testes Python da fase 3, com PostgreSQL real: 343 passando (unitários e integração); ruff, mypy e `generate-artifacts --check` limpos | | `docker compose -p cycleops-test -f compose.test.yaml up -d --wait`; `CYCLEOPS_TEST_DATABASE_URL=postgresql://postgres:local-only-test@127.0.0.1:55432/postgres uv run --project apps/backend pytest apps/backend` | 01b4497 | 2026-10-08 |
| Testes da web na fase 3: ESLint e tsc sem erro, 63 testes no Vitest, 12 E2E contra o build do Pages | | em `apps/web`: `corepack pnpm lint`, `typecheck`, `test`, `e2e` | 01b4497 (rodou antes do commit, mesmo código) | 2026-10-08 |
| E2E contra o Compose sintético sem internet: 11 passando (simulação por SSE, worker parado, conexão congelada, troca de fonte, quarentena, 5 abas com X-Forwarded-For forjado, SSE sem compressão, CSP, headers, saída do worker bloqueada, reset do banco); smoke ok | `apps/web/tests/compose/` | `CYCLEOPS_NET=10.89.2 docker compose -p cycleops-e2e -f compose.yaml -f compose.offline.yaml up -d --build --wait`; `scripts/smoke`; em `apps/web`: `corepack pnpm e2e:compose` | 01b4497 (rodou antes do commit, mesmo código) | 2026-10-08 |
| Latência no Compose: recebimento até pintura p95 128,6 ms; commit até escrita no socket p95 11,3 ms (40 eventos em 10 min) | `docs/results/performance.md`, `docs/results/data/compose-latency.json`, `docs/results/data/compose-commit-to-write.json` | em `apps/web`: `CYCLEOPS_MEASURE=1 corepack pnpm e2e:compose latency` | 01b4497 | 2026-10-08 08:57 a 09:07 |
| `docker stats` do Compose em repouso, 10 amostras depois de 5 min parado | `docs/results/performance.md`, `docs/results/data/compose-docker-stats.json` | `docker stats --no-stream` sobre a pilha `cycleops-e2e` | 01b4497 | 2026-10-08 08:55 |
| Orçamento do bundle na fase 3: 118.024 bytes no build do Pages e 118.305 no build do edge, teto de 127.026 | `apps/web/bundle-budget.json` | em `apps/web`: `corepack pnpm build:pages && corepack pnpm check:bundle`; `corepack pnpm build && corepack pnpm check:bundle` | 01b4497 | 2026-10-08 |
| Teste manual do live contra Oslo: 37 requisições em 7 min 46 s, nenhum warning ou erro | `docs/results/live-manual.md` | `docker compose -p cycleops-live --env-file .env.live up -d --build --wait` | 01b4497 | 2026-10-08 09:08 a 09:16 |
| gitleaks 8.30.1 no histórico: 11 commits, nenhum vazamento; actionlint e shellcheck sem achado | | imagem do `ci.yml`; `rhysd/actionlint`; `koalaman/shellcheck` em `scripts/` | 01b4497 | 2026-10-08 09:18 |

## Pendente

A coleta de dia, de outras redes e a gravação de 60 a 90 min estão em "Coleta pendente", em `feed-validation.md`.
