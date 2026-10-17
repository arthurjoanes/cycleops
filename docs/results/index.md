# Resultados

Cada execução registrada no repositório, com o commit do código usado. Comandos rodam da raiz; `uv` pode ser `python -m uv`.

| Resultado | Arquivo | Comando | Commit | Data (UTC) |
|---|---|---|---|---|
| Coleta única dos 5 feeds | `fixtures/recorded/inspect-20261007T032439Z.ndjson.gz` | `CYCLEOPS_CLIENT_IDENTIFIER=arthurjoanes-cycleops uv run --project apps/backend inspect-feed --deadline-seconds 30` | 0da0d2c (rodou antes do commit, com o mesmo cliente) | 2026-10-07 03:24 |
| Amostra de 10 min | `fixtures/recorded/sample-20261007T032452Z.ndjson.gz` | `CYCLEOPS_CLIENT_IDENTIFIER=arthurjoanes-cycleops uv run --project apps/backend record-feed --minutes 10 --deadline-seconds 30 --out fixtures/recorded/sample-20261007T032452Z.ndjson.gz` | 0da0d2c (idem) | 2026-10-07 03:24 a 03:34 |
| Validação do feed | `docs/results/feed-validation.md` | `uv run --project apps/backend inspect-feed --recording fixtures/recorded/inspect-20261007T032439Z.ndjson.gz fixtures/recorded/sample-20261007T032452Z.ndjson.gz` | 98717c1 | 2026-10-07 |
| Testes Python, ruff e mypy: 130 testes passando, sem rede | | `uv run --project apps/backend pytest apps/backend`, `uv run --project apps/backend ruff check apps/backend`, `uv run --project apps/backend mypy --config-file apps/backend/pyproject.toml apps/backend/src apps/backend/tests` | 98717c1 | 2026-10-07 |
| Artefato sintético da demo: 242 eventos, 85 KB em gzip, fechamento à 01:00 e incidentes resolvidos | `apps/web/public/replay/synthetic/` | `uv run --project apps/backend generate-artifacts` | c5845b1 | 2026-10-08 |
| Aceite da amostra gravada de 10 min: não passa (40 incidentes confirmados, nenhum resolvido), saída 3 | `fixtures/recorded/sample-20261007T032452Z.ndjson.gz` | `uv run --project apps/backend compile-replay --recording fixtures/recorded/sample-20261007T032452Z.ndjson.gz --out data/replay-check` | c5845b1 | 2026-10-08 |
| Testes Python, ruff e mypy com o motor e o replay: 215 testes passando, sem rede; `generate-artifacts --check` sem diferença | | `uv run --project apps/backend pytest apps/backend`, `uv run --project apps/backend generate-artifacts --check` | c5845b1 | 2026-10-08 |
| Base do mapa: água, costa, parques e vias principais do OpenStreetMap, 1,9 MB | `apps/web/public/basemap/` | `scripts/basemap/generate.sh` | 5794b7a (rodou antes do commit) | 2026-10-08 01:30 |
| Testes Python, ruff e mypy com a web nova (backend sem mudança): 215 testes passando; `generate-artifacts --check` sem diferença | | os mesmos da linha acima | 5794b7a (rodou antes do commit; o backend não mudou) | 2026-10-08 |
| Testes da web: ESLint e tsc sem erro, 30 testes no Vitest, 12 E2E no Playwright contra o build do Pages sob `/cycleops/` | | em `apps/web`: `corepack pnpm lint`, `typecheck`, `test`, `e2e` | 5794b7a | 2026-10-08 |
| Orçamento do bundle: 116.775 bytes em gzip, teto de 127.026 | `apps/web/bundle-budget.json` | em `apps/web`: `corepack pnpm build:pages && corepack pnpm check:bundle` | 5794b7a | 2026-10-08 |
| Teste de carga do frontend: recebimento até pintura com p95 de 157 a 232 ms; gatilho de virtualização atingido em "Todas as estações" com 1.000 estações | `docs/results/performance.md`, `docs/results/data/frontend-load.json` | em `apps/web`: `corepack pnpm perf` | 5794b7a | 2026-10-08 02:24 |
| Web Vitals de laboratório, mediana de 3: LCP 3.060 ms, CLS 0,069, INP 176 ms | `docs/results/performance.md`, `docs/results/data/web-vitals.json` | o mesmo `corepack pnpm perf` | 5794b7a | 2026-10-08 02:25 |
| Experimento revertido: `content-visibility: auto` em "Todas as estações", 1.000 estações | `docs/results/data/frontend-load-content-visibility.json` | em `apps/web`, com a mudança local: `corepack pnpm perf -g "1.000.*view all"`; a saída cai em `data/frontend-load.json`, foi renomeada para `frontend-load-content-visibility.json`, e o `frontend-load.json` da execução completa voltou | 5794b7a com a mudança local | 2026-10-08 |
| gitleaks 8.30.1 no histórico: 9 commits, nenhum vazamento | | imagem do `ci.yml` com `GIT_CONFIG_*` de `safe.directory`, `git --redact` | 5794b7a | 2026-10-08 02:33 |
| Capturas da interface no modo Simulação, 1440, 768 e 320 px | `docs/media/` | em `apps/web`: `corepack pnpm exec playwright test --project=media` | 5794b7a (o projeto `media` entrou no cf478dc; a web é a mesma) | 2026-10-08 |
| Testes Python com o Compose, com PostgreSQL real: 343 passando (unitários e integração); ruff, mypy e `generate-artifacts --check` limpos | | `docker compose -p cycleops-test -f compose.test.yaml up -d --wait`; `CYCLEOPS_TEST_DATABASE_URL=postgresql://postgres:local-only-test@127.0.0.1:55432/postgres uv run --project apps/backend pytest apps/backend` | 01b4497 | 2026-10-08 |
| Testes da web com a `LiveSseSource`: ESLint e tsc sem erro, 63 testes no Vitest, 12 E2E contra o build do Pages | | em `apps/web`: `corepack pnpm lint`, `typecheck`, `test`, `e2e` | 01b4497 (rodou antes do commit, mesmo código) | 2026-10-08 |
| E2E contra o Compose sintético sem internet: 11 passando (simulação por SSE, worker parado, conexão congelada, troca de fonte, quarentena, 5 abas com X-Forwarded-For forjado, SSE sem compressão, CSP, headers, saída do worker bloqueada, reset do banco); smoke ok | `apps/web/tests/compose/` | `CYCLEOPS_NET=10.89.2 docker compose -p cycleops-e2e -f compose.yaml -f compose.offline.yaml up -d --build --wait`; `scripts/smoke`; em `apps/web`: `corepack pnpm e2e:compose` | 01b4497 (rodou antes do commit, mesmo código) | 2026-10-08 |
| Latência no Compose: recebimento até pintura p95 128,6 ms; commit até escrita no socket p95 11,3 ms (40 eventos em 10 min) | `docs/results/performance.md`, `docs/results/data/compose-latency.json`, `docs/results/data/compose-commit-to-write.json` | em `apps/web`: `CYCLEOPS_MEASURE=1 corepack pnpm e2e:compose latency` | 01b4497 | 2026-10-08 08:57 a 09:07 |
| `docker stats` do Compose em repouso, 10 amostras depois de 5 min parado | `docs/results/performance.md`, `docs/results/data/compose-docker-stats.json` | `docker stats --no-stream` sobre a pilha `cycleops-e2e` | 01b4497 | 2026-10-08 08:55 |
| Orçamento do bundle com o Compose: 118.024 bytes no build do Pages e 118.305 no build do edge, teto de 127.026 | `apps/web/bundle-budget.json` | em `apps/web`: `corepack pnpm build:pages && corepack pnpm check:bundle`; `corepack pnpm build && corepack pnpm check:bundle` | 01b4497 | 2026-10-08 |
| Teste manual do live contra Oslo: 37 requisições em 7 min 46 s, nenhum warning ou erro | `docs/results/live-manual.md` | `docker compose -p cycleops-live --env-file .env.live up -d --build --wait` | 01b4497 | 2026-10-08 09:08 a 09:16 |
| gitleaks 8.30.1 no histórico: 11 commits, nenhum vazamento; actionlint e shellcheck sem achado | | imagem do `ci.yml`; `rhysd/actionlint`; `koalaman/shellcheck` em `scripts/` | 01b4497 | 2026-10-08 09:18 |
| Testes Python no fechamento do MVP, com PostgreSQL real: 343 passando | | `docker compose -p cycleops-test -f compose.test.yaml up -d --wait`; `CYCLEOPS_TEST_DATABASE_URL=postgresql://postgres:local-only-test@127.0.0.1:55432/postgres uv run --project apps/backend pytest apps/backend` | 60706cb | 2026-10-08 21:53 |
| Testes da web no fechamento do MVP: ESLint e tsc sem erro, 65 testes no Vitest, 12 E2E contra o build do Pages (com a checagem de nenhuma requisição à API) | | em `apps/web`: `corepack pnpm lint`, `typecheck`, `test`, `e2e` | 60706cb (rodou antes do commit, mesmo código) | 2026-10-08 |
| Compose sem internet no fechamento do MVP: smoke ok, saída do worker bloqueada (`Network is unreachable`), 11 E2E passando | `apps/web/tests/compose/` | `CYCLEOPS_NET=10.89.2 docker compose -p cycleops-e2e -f compose.yaml -f compose.offline.yaml up -d --build --wait`; `scripts/smoke`; em `apps/web`: `corepack pnpm e2e:compose` | 60706cb | 2026-10-08 21:59 a 22:03 |
| Vídeo da aplicação: 26,5 s, 1280×720, VP8, 2,4 MB | `docs/media/demo.webm` | com a pilha recém-criada: em `apps/web`, `CYCLEOPS_DEMO=1 corepack pnpm exec playwright test --project=media demo` | 60706cb | 2026-10-08 22:06 |
| gitleaks 8.30.1 no histórico: 13 commits, nenhum vazamento; actionlint sem achado | | imagem do `ci.yml`; `rhysd/actionlint` | 60706cb | 2026-10-08 22:07 |

## Revisão de 2026-10-09

| Resultado | Arquivo | Comando | Commit | Data (UTC) |
|---|---|---|---|---|
| Lacunas do motor, relógio do replay, histórico com heartbeat e limites de gravação; 353 testes Python com PostgreSQL, 69 unitários web, 13 E2E Pages e 11 E2E Compose | `docs/results/validacao-20261009.md` | comandos registrados no relatório | 6249cf6 | 2026-10-09 |
| Recuperação de 25 respostas da coleta interrompida: sha256 conferido, nenhum incidente resolvido, aceite com saída 3 | `fixtures/recorded/recovered-20261009T093810Z.ndjson.gz`, `docs/results/feed-validation.md` | `apps/backend/.venv/Scripts/inspect-feed.exe --recording fixtures/recorded/recovered-20261009T093810Z.ndjson.gz`; `apps/backend/.venv/Scripts/compile-replay.exe --recording fixtures/recorded/recovered-20261009T093810Z.ndjson.gz --out data/recovered-check` | d7ad657 | 2026-10-09 |
| Latência da versão corrigida, 40 eventos em 10 min: recebimento até pintura p95 141,3 ms; commit até escrita p95 106,2 ms | `docs/results/performance.md`, `docs/results/data/compose-latency-20261009.json`, `docs/results/data/compose-commit-to-write-20261009.json` | em `apps/web`: `CYCLEOPS_MEASURE=1 corepack pnpm e2e:compose latency` | d7ad657, mesma aplicação de 6249cf6, com documentação em edição | 2026-10-09 22:43 a 22:53 |
| Gravação de 60 min: 249 respostas 200, sem falhas; hash conferido e par de 782.028 bytes | `fixtures/recorded/record-20261009T222527Z.ndjson.gz`, `docs/results/data/feed-20261009.txt` | `CYCLEOPS_CLIENT_IDENTIFIER=arthurjoanes-cycleops uv run --project apps/backend record-feed --minutes 60 --out data/recordings/record-20261009T222700Z.ndjson.gz`; `inspect-feed --recording fixtures/recorded/record-20261009T222527Z.ndjson.gz` (cópia renomeada conforme primeiro requestedAt) | f64943d, mesmo coletor de 6249cf6 | 2026-10-09 22:25 a 23:25 |
| Replay real aceito: 218 eventos, 40 incidentes confirmados, 2 resolvidos, fechamento à 01:00 de Oslo; eventos gzip de 29.451 bytes | `apps/web/public/replay/recorded/`, `docs/results/feed-validation.md` | `uv run --project apps/backend compile-replay --recording fixtures/recorded/record-20261009T222527Z.ndjson.gz --out data/replay-check`; `generate-artifacts`; `generate-artifacts --check`; em `apps/web`: `corepack pnpm generate:contracts` | f64943d | 2026-10-09 23:25 |
| Verificação final com replay real: 21 testes Python do replay, 14 E2E Pages; bundle de 118.063 bytes gzip | `docs/results/validacao-20261009.md` | `uv run --project apps/backend pytest apps/backend/tests/test_replay.py -q`; em `apps/web`: `corepack pnpm e2e`, `corepack pnpm check:bundle` | f64943d | 2026-10-09 23:26 |
| Replay real no Caddy em 1440, 768 e 320 px: modo, data, mapa e nove verificações de overflow; nenhum erro de console ou CSP | `docs/results/validacao-20261009.md`, `docs/media/recorded-1440.png`, `docs/media/recorded-320.png` | Playwright: `page.goto`, pausa, salto para Jakob kirke e `page.screenshot({ fullPage: true })`, conforme sequência no relatório | f64943d (mesmo artefato, ainda sem commit durante a captura) | 2026-10-09 23:27 |

## Identidade visual

| Resultado | Arquivo | Comando | Commit | Data (UTC) |
|---|---|---|---|---|
| Identidade visual: 69 testes unitários, 14 E2E Pages e 2 capturas; bundle de 119.270 bytes gzip; replay no Compose em três larguras sem overflow | [Relatório](identity-20261009.md), `docs/media/identity-desktop.png`, `docs/media/identity-mobile.png` | `corepack pnpm --dir apps/web exec playwright test --project=e2e --project=media tests/e2e/pages.spec.ts tests/media/identity.spec.ts`; demais comandos no relatório | c830eaa | 2026-10-09 |
| Web Vitals antes da reserva de espaço no cabeçalho, com outros Chromium em execução: medianas LCP 3.692 ms, CLS 0,123 e INP 384 ms | `docs/results/data/web-vitals-identity-20261009.json` | `corepack pnpm --dir apps/web exec playwright test --project=perf tests/performance/vitals.spec.ts` | 9e7cf8e com alterações locais | 2026-10-09 23:44:45 |
| Web Vitals final, isolado dos outros Chromium: medianas LCP 3.248 ms, CLS 0,004 e INP 240 ms; limites de comparação no relatório | `docs/results/data/web-vitals-identity-final-20261009.json`, [Relatório](identity-20261009.md#web-vitals) | mesmo comando da linha anterior | c830eaa | 2026-10-09 23:49:57 |

## Publicação de 2026-10-10

| Resultado | Arquivo | Comando | Commit | Data (UTC) |
|---|---|---|---|---|
| CI: 354 testes Python, 69 unitários web, 14 E2E Pages, 11 E2E Compose e gitleaks sem segredos | [Publicação](publicacao.md) | [workflow ci](https://github.com/arthurjoanes/cycleops/actions/runs/38007237961) | 185e5af | 2026-10-10 |
| Pages publicado; mapa, replay, logo e favicon conferidos no endereço público, em 1440 e 320 px | [Dados da verificação](data/public-pages-20261010.json) | [workflow pages](https://github.com/arthurjoanes/cycleops/actions/runs/38007708719); Playwright conforme roteiro em `publicacao.md` | 185e5af | 2026-10-10 00:08 |

## Exploração dos dados em 2026-10-11

O [relatório de exploração](exploracao-20261011.md) registra a revisão dos links de momentos, timeline, resumo da gravação, evolução dos indicadores e qualidade da coleta. Comandos, revisão do código e resultados dos testes ficam no relatório.

A gravação usada nessa revisão é a mesma de 09/10/2026: 249 respostas HTTP 200, 225 publicações aceitas, 24 repetidas, 218 eventos, 40 incidentes confirmados e dois resolvidos. O [guia de exploração](../explorar-dados.md) explica as janelas e os denominadores.

Capturas: [resumo da gravação](../media/analysis-summary.png), [qualidade da coleta](../media/analysis-quality.png) e [celular](../media/analysis-mobile.png).

## Interface em 2026-10-12

O [relatório da interface](uiux-20261012.md) registra a revisão da página principal, controles do replay, navegação por teclado e comportamento em 1440, 768 e 320 px. O [guia da interface](../ui-ux.md) descreve as ações e os painéis expansíveis.

Capturas: [desktop](../media/uiux-desktop.png), [tablet](../media/uiux-tablet.png), [celular](../media/uiux-mobile.png) e [análise](../media/uiux-analysis.png).

110 testes unitários web, 28 cenários de navegação e quatro capturas passaram. Bundle inicial: 125.599 bytes gzip no Pages e 125.941 no servidor, com teto de 127.026. Medianas de laboratório: LCP 6.004 ms, CLS 0,000 e INP 152 ms; LCP segue acima da meta nessa rede emulada. [Web Vitals](data/web-vitals-uiux-final-20261012.json) · [Carga](data/frontend-load-uiux-20261012.json) · [Comparação dos contratos](data/contract-validation-parity-20261012.json).

## Pendente

Manutenção local: [17/10/2026](manutencao-20261017.md), correção de datas da API, patches de Vite/Vitest e comandos executados sobre `6343194`.

- A coleta de dia e de outras redes está em "Coleta pendente", em `feed-validation.md`. A gravação longa da demo passou no aceite e já tem artefato.
