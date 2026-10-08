# ADR 0005: estado e renderização no frontend

Status: Proposto, 2026-10-08.

## Contexto

Oslo tem 267 estações. O feed publica a cada 15 s, e numa publicação de dia mudam poucas estações; na abertura ou depois de uma reconexão, o lote pode trazer todas. A interface mostra a mesma informação na fila, na lista "Todas as estações", no mapa e nos detalhes, e a meta é a tela pintar até 250 ms depois de receber um lote (p95). O replay a 20× junta mais eventos por segundo do que o live.

## Decisão

- Um store Zustand com `Map<stationId, StationState>` normalizado, o catálogo e a saúde da fonte. Os eventos entram por `applyEvents`, uma função pura que só aceita a sequência seguinte e devolve o que fez com cada evento (aplicado, duplicado, buraco ou de outro fluxo).
- Os componentes leem por seletor de estação. Uma linha só renderiza de novo quando a estação dela mudou.
- A notificação é coalescida em 100 ms e o commit sai em `requestAnimationFrame`. Os 100 ms não mudam a cadência da fonte.
- A fila e "Todas as estações" renderizam a lista inteira, sem virtualização. A ordem da fila vem do `sortKey` calculado pelo motor; o navegador só compara strings.
- Sem Web Worker próprio. O único worker é o do MapLibre, que faz o layout dos tiles.
- O mapa atualiza cor e opacidade por `feature-state` e troca o símbolo com `updateData` só nas estações cujo estado mudou. O React não renderiza o mapa a cada lote.
- A partir de 768 px a página tem a altura da janela e a fila rola dentro da coluna dela. Uma fila que cresce e encolhe a cada lote mudava a altura da grade e fazia o MapLibre recriar o canvas; no perfil de CPU, era o maior custo por lote.

Estado em 2026-10-08, com a CPU 4× mais lenta (`docs/results/performance.md`, commit 5794b7a): recebimento até pintura com p95 entre 157 e 232 ms nos quatro cenários, abaixo dos 250 ms. A fila de prioridades não gera long task com 267 estações. "Todas as estações" com 1.000 estações gera uma long task por lote, de até 105 ms, e o render fica em 22,9 ms no p95: o gatilho de virtualização abaixo foi atingido nesse cenário. Com 267 estações, a lista completa dá long tasks de até 82 ms quando todas mudam no mesmo lote. Continuar com a lista completa para Oslo ou virtualizar "Todas as estações" é decisão do Arthur.

## Alternativas

- Virtualização da lista (TanStack Virtual): menos nós no DOM, mas busca do navegador, leitores de tela e o congelamento da fila ficam mais difíceis. Com 267 estações a lista inteira fica dentro dos 250 ms.
- Web Worker para aplicar eventos, filtrar e ordenar: tira trabalho da thread principal, mas obriga a copiar o estado entre threads a cada lote. O custo de aplicar um lote medido hoje fica em poucos milissegundos.
- `content-visibility: auto` nas linhas de "Todas as estações": testado no cenário de 1.000 estações. Tirou a maior parte das long tasks, mas o recebimento até pintura teve picos de 2,3 s. Revertido.
- Context do React ou Redux: Context renderiza todos os consumidores a cada mudança; Redux faz o mesmo que o Zustand com mais código.
- Recalcular prioridade no navegador: duplicaria as regras do motor Python (ver ADR 0004).

## Consequências

- O DOM cresce com o número de estações em "Todas as estações". Com 1.000 estações a página tem 4.112 nós.
- A ordem e o texto da fila dependem do motor; mudar a regra exige regenerar os artefatos.
- O teste E2E "the map keeps its size while the queue changes" falha se o mapa voltar a mudar de tamanho com a fila.

## Risco

- Uma cidade bem maior que Oslo, ou um lote com todas as estações a cada poucos segundos, pode passar do orçamento de 250 ms sem virtualização.
- Os números são de laboratório, numa máquina só, com CPU 4× mais lenta emulada. Celulares fracos de verdade podem ficar abaixo disso.

## Quando revisar

- TanStack Virtual: em "Todas as estações", no cenário de 1.000 estações, aplicação de lote acima de 16 ms no p95 ou long task acima de 50 ms atribuída à lista.
- Web Worker próprio: long task acima de 50 ms atribuída a filtro ou ordenação no cenário de 1.000 estações.
- Recebimento até pintura acima de 250 ms no p95 em qualquer cenário medido.
