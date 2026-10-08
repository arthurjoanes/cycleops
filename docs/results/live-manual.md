# Teste manual do live

Teste curto do Compose contra o feed real de Oslo, com a interface aberta no navegador.

- Data: 2026-10-08, das 09:08:14 às 09:16:42 UTC (das 11:08 às 11:16 em Oslo, dentro do horário de serviço).
- Commit: 01b4497.
- Comando, da raiz, com `.env.live` copiado de `.env.live.example` e `CYCLEOPS_CLIENT_IDENTIFIER=arthurjoanes-cycleops`:

  ```bash
  docker compose -p cycleops-live --env-file .env.live up -d --build --wait
  ```

- Navegador: Chromium, em http://127.0.0.1:8080, 1440×900.

## O que foi conferido

1. A pilha ficou saudável em 20 s. O worker pegou a liderança, criou o fluxo `oslobysykkel` e buscou os cinco feeds uma vez na partida.
2. Cabeçalho: modo "Ao vivo", conexão "conectada", publicação "11:09:30 (horário de Oslo, UTC+2; última publicação)", qualidade "fonte em dia", cobertura 267/267.
3. Fila: as primeiras confirmações saíram às 11:09, com 3 publicações em 45 s. Às 11:10, os indicadores mostravam 41 estações sem bicicletas e 9 sem vagas confirmadas, e a fila tinha 50 linhas com nome, condição, "observado desde", idade da publicação e motivo.
4. Detalhes de Lørenporten: o `/history` respondeu em 17 ms. Última publicação 11:09:45, recebimento 11:09:55, "gerado pelo provedor" 11:09:00. Com 0 bicicletas e 19 vagas numa capacidade de 20, a tela mostrou "20; 1 não informado".
5. Restart do worker às 09:10:17 UTC: a requisição seguinte de `station_status` saiu às 09:10:25, 15 s depois da anterior (09:10:10). Nenhum feed de descoberta foi buscado de novo.
6. Restart da API às 09:11:19 UTC: a API fechou o stream com `stream_closed` (`shutdown`), a conexão foi para "reconectando" e voltou a "conectada" às 09:11:25.

## Números da execução

Lidos do banco no fim, antes do `down -v`:

| Feed | Resultado | Tentativas |
|---|---|---|
| `station_status` | ok | 30 |
| `station_status` | duplicate | 2 |
| `station_information` | ok | 2 |
| `gbfs`, `system_information`, `vehicle_types` | ok | 1 cada |

- 37 requisições ao feed em 7 min 46 s, de 09:08:24 a 09:16:10 UTC. `station_status` a cada 15 s, uma requisição por vez.
- 32 eventos no `event_log`.
- Incidentes: 41 `pickup_unavailable` e 10 `return_unavailable` confirmados, 1 `return_unavailable` descartado, nenhum resolvido no período.
- Logs do worker e da API: nenhuma linha de warning ou erro.

## Observações

- A primeira publicação de `station_status` chegou antes do cadastro: o evento 2 saiu sem estações e o cadastro entrou no evento 3, um segundo depois.
- As duas respostas `duplicate` repetiram o `last_updated` da anterior, como na coleta de 2026-10-07 (`feed-validation.md`).
- A idade da publicação na fila ficou entre 10 e 25 s, a mesma faixa medida em 2026-10-07 entre o `last_updated` e o recebimento.
- Na linha do tempo de 6 h, o período anterior ao início do fluxo aparece como "sem dado".

## Fora deste teste

- Fechamento às 01:00 e abertura às 05:00 contra o feed real.
- Worker parado, conexão congelada e quarentena com o feed real. Os três têm E2E contra a simulação no Compose.
- Uma resolução de incidente: em 8 min nenhum incidente chegou a `resolved`.
