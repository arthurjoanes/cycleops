# Validação de 2026-10-09

Código: `6249cf6`; gravação aceita e artefatos: `f64943d`. Windows 11, Python 3.14.8, Node 24, Chromium 153.0.8010.12 headless e Docker Desktop com Engine 29.7.2. Os testes usam transporte simulado ou serviços locais; a coleta real roda separada, pela CLI.

## Correções

- Uma publicação recebida antes do primeiro tick após uma parada longa podia confirmar ou resolver um incidente usando o intervalo sem observações. O motor agora interrompe esse trecho antes de aplicar o registro novo, inclusive com relatos por estação. A mudança de `observedSince` chega ao navegador mesmo quando a disponibilidade e o estado do incidente se mantêm.
- A histerese de recuperação da fonte reinicia quando outra lacuna separa as publicações usadas na contagem.
- Pausar ou terminar o replay descarrega os eventos que aguardam atualização visual. Um timer atrasado deixa o relógio no instante final da gravação.
- Um heartbeat com fonte vencida aparece como lacuna no gráfico dos detalhes, mesmo sem alteração da estação. A transição também refaz a consulta do histórico.
- A checagem de tamanho soma a gravação e seu manifesto. O limite é 5.242.880 bytes por par; manifesto ausente ou sem gravação também falha.
- Pages e Compose gravam os resultados do Playwright em diretórios separados. Os cenários sintéticos escolhem a fonte explicitamente; o teste de abertura confere o replay real quando disponível.

## Testes

| Verificação | Resultado |
|---|---|
| Python com PostgreSQL real | 353 passaram em 501,75 s, sem skips |
| Ruff e formatação | 78 arquivos, sem erro |
| mypy | 74 arquivos, sem erro |
| ESLint e TypeScript | sem erro |
| Vitest | 69 passaram |
| Playwright Pages sob `/cycleops/` | 14 passaram em 51,0 s, incluindo confirmação e resolução de um incidente real, gzip sem Content-Encoding, mapa, CSP e nenhuma chamada à API |
| Playwright Compose offline | 11 passaram; a medição de latência é habilitada separadamente |
| Smoke pelo Caddy | passou |
| Saída de rede do worker offline | bloqueada: `Network is unreachable` |
| Limite de gravação e manifesto | 3 testes passaram |
| Artefatos e contratos | regenerados; `generate-artifacts --check` sem diferença |
| Replay após importar a gravação final | 21 testes Python passaram em 21,30 s; gravação e manifesto dentro do limite |
| Bundle Pages | 118.063 bytes gzip, teto de 127.026 |
| actionlint e ShellCheck | sem erro |
| Gitleaks | nenhum vazamento no histórico verificado |
| Latência em 10 min, 40 eventos | recebimento até pintura p95 de 141,3 ms; commit até escrita p95 de 106,2 ms, dentro das metas |

Comandos da raiz, em PowerShell:

```powershell
docker compose -p cycleops-test -f compose.test.yaml up -d --wait
$env:CYCLEOPS_TEST_DATABASE_URL = 'postgresql://postgres:local-only-test@127.0.0.1:55432/postgres'
apps/backend/.venv/Scripts/python.exe -m pytest apps/backend
apps/backend/.venv/Scripts/ruff.exe check apps/backend
apps/backend/.venv/Scripts/ruff.exe format --check apps/backend
apps/backend/.venv/Scripts/python.exe -m mypy --config-file apps/backend/pyproject.toml apps/backend/src apps/backend/tests
python -m uv run --project apps/backend generate-artifacts
corepack pnpm --dir apps/web generate:contracts
python -m uv run --project apps/backend generate-artifacts --check
corepack pnpm --dir apps/web lint
corepack pnpm --dir apps/web typecheck
corepack pnpm --dir apps/web test
corepack pnpm --dir apps/web e2e
corepack pnpm --dir apps/web check:bundle
python -m uv run --project apps/backend python -m unittest discover -s scripts/tests
python -m uv run --project apps/backend python scripts/check-recordings.py
$env:CYCLEOPS_NET = '10.89.2'
docker compose -p cycleops-e2e -f compose.yaml -f compose.offline.yaml up -d --build --wait
corepack pnpm --dir apps/web e2e:compose
```

`scripts/smoke` rodou no Git Bash com `MSYS_NO_PATHCONV=1`. O Chromium foi instalado com o arquivo oficial do Chrome for Testing da mesma versão esperada pelo Playwright, depois de o downloader expirar.

A primeira rodada Python teve uma falha no teste de perda do lock: o cancelamento chegava durante a query que encerrava a sessão, antes do `sleep` observado pelo teste. O bloco que captura `CancelledError` passou a envolver a coroutine inteira. As exigências de `LockLost`, cancelamento observado e duração abaixo de 10 s continuam iguais; a rodada integral acima passou depois da correção.

A medição de latência rodou com `CYCLEOPS_MEASURE=1 corepack pnpm --dir apps/web e2e:compose latency`. Ambiente, percentis e dados brutos estão em [performance.md](performance.md).

## Coleta

O trecho recuperado da gravação interrompida e as medições do feed estão em [feed-validation.md](feed-validation.md). A nova gravação de 60 min terminou com 249 respostas 200, sem falhas, e gerou 218 eventos com dois incidentes resolvidos. O hash foi conferido antes da cópia para `fixtures/recorded/`; o arquivo original permanece em `data/recordings/`.

Depois da importação, os artefatos foram regenerados, `generate-artifacts --check` passou e os 21 testes de `apps/backend/tests/test_replay.py` foram repetidos. O E2E do Pages passou com a gravação real como modo inicial. A imagem do Caddy foi reconstruída e a pilha local ficou disponível em `http://127.0.0.1:8080`, com worker sintético; o replay real é escolhido no seletor ou por `#/?mode=recorded`.

## Capturas do replay real

O Playwright conferiu também o Caddy em 09/10/2026, das 23:27:16 às 23:27:24 UTC, com Chromium 153.0.8010.12 e o mesmo artefato de `f64943d`. Nas larguras 1440, 768 e 320 px, o replay mostrou a data 10/10/2026 de Oslo, cobertura 267/267 e mapa com atribuição. As nove verificações de rolagem horizontal deram zero. Não houve erro de console, JavaScript, CSP, resposta HTTP de erro ou chamada à API ou a outra origem.

Sequência usada no navegador: `page.goto("http://127.0.0.1:8080/#/?mode=recorded")`, pausar, selecionar Jakob kirke em "Incidente" e clicar em "Ir para o incidente". O player ficou no evento 106, com velocidade 5× e sem tocar. Foram feitas capturas com `page.screenshot({ fullPage: true })` dos detalhes, da visão geral e da lista. No celular, a visão geral usa a aba "Mapa".

As imagens de [1440 px](../media/recorded-1440.png) e [320 px](../media/recorded-320.png) foram inspecionadas e guardadas sem substituir as capturas anteriores da simulação. Essa conferência não preenche o checklist de avaliação humana de acessibilidade.

Os passos de publicação e os testes que precisam de uma pessoa ficam em [publicacao.md](publicacao.md).
