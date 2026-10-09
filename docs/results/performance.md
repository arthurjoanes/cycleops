# Desempenho (laboratório)

Números de laboratório, numa máquina só. Não são dados de campo. As duas primeiras seções são do frontend contra o build do Pages servido sob `/cycleops/` por `apps/web/scripts/serve-pages.mjs`; as do fim, do Compose.

Ambiente das medições do frontend:

- CPU AMD Ryzen 7 5700X3D (16 threads), 31,9 GB de RAM, Windows 11 (win32 10.0.26300);
- Chromium 156.0.8078.4 headless, pelo Playwright 1.64;
- CPU 4× mais lenta, emulada pelo DevTools Protocol;
- commit 5794b7a, 2026-10-08 às 02:24 UTC.

Comando, em `apps/web`: `corepack pnpm perf`. Dados brutos: `data/frontend-load.json` e `data/web-vitals.json`.

## Teste de carga

Um gerador TypeScript com seed 7 manda lotes direto no store pelo mesmo caminho do replay (janela de 100 ms e commit em `requestAnimationFrame`), sem rede (`#/?source=load`). São 30 lotes, os 5 primeiros de aquecimento, com 1 s entre lotes em vez de 15 s: o custo de um lote não depende do intervalo. Viewport de 1440×900, com o mapa na tela.

- Recebimento até pintura: do lote saindo do gerador até a primeira tarefa depois do frame seguinte ao commit. Inclui a janela de 100 ms.
- Render: atualização do store mais o render e o commit do React (a lista). O mapa atualiza no frame seguinte e fica de fora.
- Heap medido depois de forçar o GC, no fim do aquecimento e no fim.

| Cenário | Lista | Recebimento até pintura, p50 / p95 / máx (ms) | Render p95 (ms) | Long tasks (qtd., máx. ms) | Linhas renderizadas por lote (p50) | Frames/s | Nós no DOM | Heap (MB) |
|---|---|---|---|---|---|---|---|---|
| 267 estações, todas mudando | Prioridades | 138 / 157 / 161 | 8,3 | 0 | 11 | 56 | 208 | 11,8 → 12,3 |
| 267 estações, todas mudando | Todas | 188 / 210 / 212 | 16,2 | 25, 82 | 267 | 53 | 1.180 | 13,4 → 13,9 |
| 1.000 estações, 30% mudando | Prioridades | 162 / 171 / 173 | 16,7 | 1, 51 | 49 | 52 | 550 | 13,2 → 13,9 |
| 1.000 estações, 30% mudando | Todas | 218 / 232 / 234 | 22,9 | 25, 105 | 303 | 47 | 4.112 | 18,7 → 19,8 |

A atualização do store fica em até 2,3 ms no p95 em todos os cenários.

- Recebimento até pintura: p95 de 157 a 232 ms, abaixo da meta de 250 ms nos quatro cenários.
- Heap: sobe de 0,5 a 1,1 MB entre o fim do aquecimento e o fim, em 25 lotes.
- Gatilho de virtualização (ADR 0005): em "Todas as estações" com 1.000 estações, cada lote gera uma long task de até 105 ms e o render fica em 22,9 ms no p95. A long task vem da lista: com a fila de prioridades e o mesmo mapa, o cenário de 1.000 estações tem uma long task só, de 51 ms. Com a CPU 4× mais lenta, o gatilho do ADR 0005 (long task acima de 50 ms atribuída à lista ou render acima de 16 ms no p95) foi atingido. Em Oslo, com 267 estações todas mudando no mesmo lote, a lista completa dá long tasks de até 82 ms.
- Experimento sem adoção: `content-visibility: auto` nas linhas de "Todas as estações" (`data/frontend-load-content-visibility.json`, mesmo commit com a mudança local, só o cenário de 1.000 estações). As long tasks caíram para 8, com máximo de 57 ms, mas o recebimento até pintura teve picos de 2,3 s (p95 de 552 ms). A mudança foi revertida.

## Web Vitals

Três execuções com cache desligado, rede emulada com 150 ms de latência, 1,6 Mbps de download e 750 kbps de upload, viewport de 1440×900. LCP e CLS vêm do PerformanceObserver até o fim do roteiro. O INP é a maior duração entre os eventos com `interactionId` no roteiro: Todas as estações, busca "sintetica 01", Prioridades, Ir para o incidente, Fechar.

| Execução | LCP (ms) | CLS | INP (ms) |
|---|---|---|---|
| 1 | 3.060 | 0,069 | 184 |
| 2 | 3.096 | 0,069 | 176 |
| 3 | 3.056 | 0,069 | 176 |
| Mediana | 3.060 | 0,069 | 176 |

O maior evento nas três execuções é o clique em "Todas as estações", que monta as 267 linhas. As metas de campo (LCP até 2,5 s, INP até 200 ms e CLS até 0,1, no p75) só valem com visitas reais e não se comparam com estes números.

## Bundle

`corepack pnpm check:bundle` no build do commit 5794b7a: 116.775 bytes em gzip para o chunk de entrada, os imports estáticos dele e o CSS, contra o orçamento de 127.026 bytes (primeiro build, 115.478 bytes, mais 10%). O mapa, o worker do MapLibre e o gerador de carga ficam fora, em imports dinâmicos.

No commit 01b4497 (Compose): 118.024 bytes no build do Pages e 118.305 bytes no build do edge (`corepack pnpm build`), contra o mesmo orçamento. A `LiveSseSource` fica fora da conta, num import dinâmico, como o mapa.

## Compose: latência

Compose sintético com `compose.offline.yaml`, uma aba em `#/?measure=1` pelo edge em `127.0.0.1:8080`, sem throttling de CPU nem de rede (loopback). Mesma máquina das medições do frontend, Docker 29.7.2 (linux/amd64), Chromium 153.0.8010.12 headless, commit 01b4497, janela de 08:56:58 a 09:06:58 UTC de 2026-10-08.

Comando, em `apps/web`, com a pilha no ar (`docker compose -p cycleops-e2e -f compose.yaml -f compose.offline.yaml up -d --wait`, com `CYCLEOPS_NET=10.89.2`):

```bash
CYCLEOPS_MEASURE=1 corepack pnpm e2e:compose latency
```

| Medida | Meta (p95) | Amostras | p50 | p95 | Máx. |
|---|---|---|---|---|---|
| Recebimento até pintura, no navegador | 250 ms | 40 | 119,9 ms | 128,6 ms | 130,2 ms |
| Commit até escrita no socket, na API | 1 s | 40 | 9,5 ms | 11,3 ms | 11,7 ms |

- Recebimento até pintura: `performance.mark` quando a mensagem `station_batch` chega e `performance.measure` depois da pintura (rAF seguido de uma tarefa de `MessageChannel`). Inclui a janela de 100 ms do store, que é a maior parte do tempo. Dados: `data/compose-latency.json`.
- Commit até escrita: a API guarda, para cada `station_batch` escrito ao vivo num socket, o instante da escrita menos o `committedAt`, que vem do `clock_timestamp()` do PostgreSQL no mesmo host. A API reiniciou antes da janela e só a aba da medição se conectou. Eventos de catch-up ficam de fora. Dados: `data/compose-commit-to-write.json`.
- Com uma publicação a cada 15 s, 10 min dão 40 eventos, e o p95 fica perto do máximo.
- Ponta a ponta (do commit à pintura, com o offset do heartbeat) não foi medido.

## Compose: latência em 2026-10-09

Depois da correção das lacunas de observação e do replay, a mesma medição rodou por 10 min, de 22:43:08 a 22:53:09 UTC. Código `6249cf6`, executado no checkout `d7ad657`; o marcador `+ local changes` do relatório corresponde à documentação em edição. CPU Ryzen 7 5700X3D, 16 processadores lógicos, 31,9 GB de RAM, Windows 11 build 26300 e Chromium 153.0.8010.12 headless. Compose offline, seed 42, loopback e sem throttling. Havia outros containers na máquina, além da coleta pela CLI e, no início da janela, testes de integração.

| Medida | Meta (p95) | Amostras | p50 | p95 | Máx. |
|---|---|---|---|---|---|
| Recebimento até pintura | 250 ms | 40 | 126,9 ms | 141,3 ms | 146,8 ms |
| Commit até escrita no socket | 1 s | 40 | 25,2 ms | 106,2 ms | 191,1 ms |

Comando: `CYCLEOPS_MEASURE=1 corepack pnpm e2e:compose latency`, em `apps/web`. Dados brutos em `data/compose-latency-20261009.json` e `data/compose-commit-to-write-20261009.json`; os arquivos da execução anterior foram preservados. As duas medidas ficaram abaixo das metas. A carga concorrente do host impede atribuir a diferença entre as rodadas apenas à mudança de código.

## Compose: repouso em 2026-10-08

`docker stats --no-stream`, 10 amostras com cerca de 7 s entre elas, de 08:55:25 a 08:56:30 UTC, depois de 5 min parado desde o `up -d --build --wait` num volume novo (às 08:50:09). Nenhum navegador ou cliente conectado; só os healthchecks dos containers. CPU em % de um núcleo, como o Docker mostra. Commit 01b4497. Dados: `data/compose-docker-stats.json`.

| Container | CPU média | CPU máx. | Memória (primeira e última amostra) |
|---|---|---|---|
| api | 5,23% | 25,29% | 79,4 e 80,7 MiB |
| worker | 1,95% | 10,21% | 86,4 e 87,3 MiB |
| postgres | 2,30% | 4,25% | 58,6 e 59,3 MiB |
| edge | 0,26% | 2,56% | 13,3 e 12,6 MiB |

A API passa de 0,5% em 2 das 10 amostras (24,8% e 25,3%); nas outras 8 fica entre 0,21% e 0,42%. O worker gera uma publicação sintética a cada 15 s mesmo sem cliente.
