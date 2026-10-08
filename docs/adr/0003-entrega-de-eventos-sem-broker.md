# ADR 0003: entrega de eventos sem broker

Status: Proposto, 2026-10-08, com a medição do Compose.

## Contexto

O worker grava estado, decisões e eventos no PostgreSQL. A API precisa entregar esses eventos ao navegador por SSE, na ordem, sem perder nenhum e sem mandar o que não foi gravado. O feed de Oslo publica a cada 15 s, e o sintético segue a mesma cadência. Há um worker e uma API por deployment no MVP, e o live roda só no Compose local.

## Decisão

- O PostgreSQL é o único caminho de eventos. Cada entrada do worker vira uma transação que grava o estado, o `event_log` com `sequence = head_sequence + 1` e chama `pg_notify('cycleops_events', '<streamId>@<sequence>')`. O NOTIFY só sai no COMMIT, então nada que não foi gravado chega à API.
- Cada processo da API tem um tailer com uma conexão LISTEN própria. Ele lê `event_log` a partir do próprio cursor a cada 1 s e quando chega uma notificação; a notificação só acorda o tailer. Os eventos vão para os clientes por um distribuidor em memória, com fila limitada por cliente.
- O cliente reconecta com `Last-Event-ID` ou `?after=`. Até 40 eventos atrás (cerca de 10 min), a API faz o catch-up pelo `event_log`; além disso, ou com geração trocada, manda `resync_required` e o cliente pede um snapshot novo.
- Entrega pelo menos uma vez: o cliente descarta `sequence` repetida e trata salto como lacuna.
- Um cliente que para de ler perde a conexão depois de um tempo de escrita, e a fila dele tem teto de 200 mensagens ou 4 MiB; passou disso, recebe `resync_required` e fecha.
- Se o tailer fica mais de 3 s sem ler o banco, o heartbeat não sai e o stream termina sem mensagem. O navegador consulta o `/api/v1/feed-health` (503 com o banco fora) e reconecta com backoff, em vez de receber heartbeats com um estado que a API já não consegue conferir.

Estado em 2026-10-08, no Compose local com a simulação (`docs/results/performance.md`, commit 01b4497): commit até escrita no socket com p95 de 11,3 ms e máximo de 11,7 ms em 40 eventos em 10 min, contra a meta de 1 s.

## Alternativas

- Redis Streams: dá `XREAD` com bloqueio e retenção por tamanho, mas é mais um serviço, mais uma imagem e um caminho de escrita fora da transação do estado, que exigiria outbox para não publicar o que deu rollback.
- Kafka ou NATS: ordem e retenção melhores para muitos consumidores, com um custo de operação que uma publicação a cada 15 s não paga.
- Só polling, sem LISTEN: mais simples, mas com até 1 s a mais de atraso em cada evento e uma consulta por segundo por processo mesmo sem nada novo.
- Só LISTEN, sem polling: NOTIFY se perde com a conexão caída; o polling cobre o intervalo até a reconexão.
- WebSocket no lugar de SSE: o fluxo é só do servidor para o cliente, e o `EventSource` já traz reconexão e `Last-Event-ID`.

## Consequências

- Estado e evento ficam na mesma transação, sem outbox e sem dead letter.
- Cada processo da API segura uma conexão LISTEN e faz uma consulta por segundo no `event_log`.
- O `event_log` guarda 24 h. Cursor mais antigo leva a `resync_required`.
- Uma segunda instância da API recebe os mesmos eventos pelo próprio tailer, sem coordenação entre elas.

## Risco

- NOTIFY tem fila global no PostgreSQL. Com muitos eventos e um LISTEN parado, a fila enche e o NOTIFY falha na transação do worker. Com um evento a cada poucos segundos, isso não acontece no MVP.
- Um tailer atrasado atrasa todos os clientes daquele processo.

## Quando revisar

- p95 de commit até escrita no socket acima de 1 s, medido com NOTIFY.
- Peso do polling dos tailers medido no PostgreSQL (consultas por segundo ou CPU) com várias instâncias da API.
- Um segundo consumidor de eventos fora da API.
