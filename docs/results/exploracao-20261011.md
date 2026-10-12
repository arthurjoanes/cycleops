# Exploração dos dados — 2026-10-11

Base `5f106bb`, com as alterações desta entrega. O código e os testes estão no commit que introduz este relatório. Windows 11, Python 3.14.8, Node 24 e Chromium 153.0.8010.12 headless. Execução iniciada em 11/10 no horário de Brasília; comandos finais em 12/10 UTC.

## Mudanças verificadas

O replay compartilha o instante pausado com identidade da gravação, estação e opções da tela. A timeline permite navegar por tempo e pelos marcos de confirmação e resolução. O resumo integra o período inteiro, com cobertura e rankings de disponibilidade observada. Os indicadores guardam até 120 pontos aceitos; o diagnóstico separa tentativas HTTP, erros e resultados de publicação.

Os testes de navegação encontraram uma diferença entre o início do manifesto e o primeiro evento: 1,077 s nessa gravação. Os links usam `periodStart`; marcos usam o `committedAt` da sequência. Outro teste encontrou o foco voltando ao botão de análise depois de escolher uma estação. A seleção agora ocorre no fechamento do modal, com foco nos detalhes.

| Verificação local | Resultado |
|---|---|
| Python sem integração | 302 passaram; 60 testes de integração fora desta execução |
| Ruff e formatação | sem erro; 81 arquivos formatados |
| mypy | 77 arquivos, sem erro |
| ESLint e TypeScript | sem erro |
| Vitest | 101 passaram, 11 arquivos |
| Playwright Pages | 20 cenários passaram na primeira rodada; os três cenários de análise passaram após a correção de foco |
| Acessibilidade dos painéis | nenhuma ocorrência grave ou crítica no axe, em 320 px |
| Capturas | três passaram; resumo, qualidade e tela de 320 px conferidos |
| Bundle Pages | 126.939 bytes gzip, teto de 127.026 |
| Artefatos | `generate-artifacts --check` sem diferença |
| Publicação | checagem de texto, caminhos privados e `git diff --check` sem erro |

PostgreSQL e Compose são verificados no [CI do repositório](https://github.com/arthurjoanes/cycleops/actions/workflows/ci.yml), incluindo o novo teste de diagnóstico pelo Caddy. O Docker Desktop local não iniciou o engine nesta execução. O workflow do [Pages](https://github.com/arthurjoanes/cycleops/actions/workflows/pages.yml) publica após o CI passar.

## Dados usados

A gravação existente contém 249 respostas HTTP 200, 225 publicações aceitas e 24 repetidas recusadas pelo motor. Não houve falha HTTP, de transporte ou de contrato; os 65.682 registros de estações parseados eram válidos. Registros contam por resposta, incluindo a mesma estação em momentos diferentes.

O replay tem 218 eventos, 40 incidentes confirmados e dois resolvidos. Publicações e eventos têm unidades diferentes: uma publicação pode não alterar o estado, e o relógio também pode emitir eventos. O resumo separa 34 min 31 s de fonte válida, 1 s sem dado verificável e 25 min 16 s de serviço fechado. As durações exibidas são arredondadas.

Não houve nova consulta ao operador. Dados de simulação ficam identificados, inclusive no relatório de qualidade. O loader aceita artefatos antigos sem diagnóstico e rejeita SHA-256, fluxo, modo ou período incompatíveis.

## Carga do frontend

Quatro cenários passaram, com 30 lotes, cinco de aquecimento, mapa visível e CPU 4× mais lenta. Ambiente: Ryzen 7 5700X3D, 31,9 GB de RAM, Windows 11, Chromium headless. Dados e método: [frontend-load-exploracao-20261011.json](data/frontend-load-exploracao-20261011.json), medidos às 02:59 UTC de 12/10, na base indicada acima com as alterações locais desta entrega.

| Cenário | Lista | Store p95 (ms) | Recebimento até pintura p95 (ms) | Render p95 (ms) |
|---|---|---|---|---|
| 267 estações, todas mudando | Prioridades | 1,4 | 157,9 | 9,5 |
| 267 estações, todas mudando | Todas | 1,3 | 203,6 | 18,7 |
| 1.000 estações, 30% mudando | Prioridades | 1,7 | 191,5 | 22,8 |
| 1.000 estações, 30% mudando | Todas | 3,4 | 241,2 | 26,5 |

O recebimento até pintura ficou abaixo da meta de 250 ms nos quatro cenários. A lista inteira ainda produz long tasks com CPU limitada; o gatilho de virtualização já registrado no [ADR 0005](../adr/0005-estado-e-renderizacao.md) continua presente. Essa medição não representa visitas reais.

O roteiro de Web Vitals rodou três vezes, com cache desligado, CPU 4× mais lenta e rede emulada a 1,6 Mbps com 150 ms de latência. Medianas: LCP de 3.576 ms, CLS de 0,092 e INP de 200 ms. Ambiente, interações e valores de cada execução estão em [web-vitals-exploracao-20261011.json](data/web-vitals-exploracao-20261011.json). São medidas de laboratório; as metas de campo exigem visitas reais.

## Reproduzir

Da raiz, em PowerShell:

```powershell
python -m uv run --project apps/backend pytest apps/backend -m 'not integration'
python -m uv run --project apps/backend ruff check apps/backend
python -m uv run --project apps/backend ruff format --check apps/backend
python -m uv run --project apps/backend mypy --config-file apps/backend/pyproject.toml apps/backend/src apps/backend/tests
python -m uv run --project apps/backend generate-artifacts --check
corepack pnpm --dir apps/web lint
corepack pnpm --dir apps/web typecheck
corepack pnpm --dir apps/web test
corepack pnpm --dir apps/web e2e
corepack pnpm --dir apps/web check:bundle
corepack pnpm --dir apps/web exec playwright test --project=media tests/media/analysis.spec.ts
corepack pnpm --dir apps/web exec playwright test --project=perf tests/performance/load.spec.ts
corepack pnpm --dir apps/web exec playwright test --project=perf tests/performance/vitals.spec.ts
```

O Playwright inicia um servidor novo em 4188. `CYCLEOPS_WEB_TEST_PORT` permite escolher outra porta disponível. A prévia manual permanece em 4173.

Capturas: [resumo](../media/analysis-summary.png), [qualidade](../media/analysis-quality.png) e [mobile](../media/analysis-mobile.png). Uso, denominadores e referências: [explorar-dados.md](../explorar-dados.md). Decisão: [ADR 0006](../adr/0006-exploracao-e-diagnostico.md).
