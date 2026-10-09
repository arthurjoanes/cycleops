# Validação do feed

Coletas contra o feed GBFS do Oslo Bysykkel, feitas de uma rede residencial (PC de desenvolvimento, Windows 11) com `CYCLEOPS_CLIENT_IDENTIFIER=arthurjoanes-cycleops`.

| Arquivo em `fixtures/recorded/` | UTC | Horário de Oslo | Requisições | Comando |
|---|---|---|---|---|
| `inspect-20261007T032439Z.ndjson.gz` | 2026-10-07 03:24:40 | qua 05:24 | 5 | `inspect-feed --deadline-seconds 30` |
| `sample-20261007T032452Z.ndjson.gz` | 2026-10-07 03:24:53 a 03:34:41 | qua 05:24 a 05:34 | 44 | `record-feed --minutes 10 --deadline-seconds 30` |
| `record-20261009T222527Z.ndjson.gz` | 2026-10-09 22:25:27 a 23:25:15 | sáb 00:25 a 01:25, em 10/10 | 249 | `record-feed --minutes 60` |

Os números da tabela de premissas abaixo vêm das duas coletas de 07/10: 49 respostas, todas 200, nenhuma falha. A janela é curta e cai logo depois da abertura do sistema (05:00), então o que depende de movimento ou da rede ficou inconclusivo. A gravação de 09/10 e o aceite do replay estão na seção "Gravação da demo".

## Premissas

| # | Premissa | Resultado | Dado |
|---|---|---|---|
| 1 | Versão em cada feed | confirmada | `version: "2.3"` nas 49 respostas, nos 5 feeds |
| 2 | `ttl` e degraus de `last_updated` | confirmada | `ttl: 15` em todas. Em `station_status`, degraus de 15 s (34) e de 30 s (3). A página realtime fala em 10 s, mas o exemplo dela é de 2019 e traz `ttl: 10` |
| 3 | Semântica de `last_reported` durante a operação | confirmada: `feed_generation` | um valor só em cada uma das 41 respostas (38 publicações distintas). Entre publicações seguidas, ele muda para todas as estações: 9861 pares estação-publicação sem mudança de contagem e 18 com mudança. Ver a nota abaixo |
| 4 | Campos opcionais | confirmada | `station_status`: `num_docks_available`, `vehicle_types_available` (`bike`, `ebike`) e `last_reported` em todas as 267; não vêm `num_bikes_disabled`, `num_docks_disabled` nem `vehicle_docks_available`; `num_vehicles_available`, fora da 2.3, em todas. `station_information`: `address`, `cross_street`, `capacity`, `is_virtual_station` e `rental_uris` em todas; não vêm `is_valet_station`, `short_name`, `region_id`, `post_code`, `rental_methods` nem os campos de veículo |
| 5 | Distribuição de `capacity - (bikes + docks)` | confirmada | última publicação (05:34): zero em 71, positiva em 196, negativa em 0; mediana 1, máximo 17 |
| 6 | Sinais de valet | inconclusiva (coleta pendente) | nenhum sinal nas 41 respostas (38 publicações): nenhuma estação sem `num_docks_available`, nenhuma com `bikes + docks > capacity` e `is_valet_station` ausente do cadastro. O valet só funciona em alguns locais e dias, e às 05:30 de outubro é provável que não houvesse nenhum |
| 7 | ETag de `station_information` | confirmada | muda a cada publicação: duas respostas a 13 s de distância, com ETag, `Last-Modified` e `last_updated` diferentes. Em `station_status`, 38 ETags para 38 `last_updated` distintos. `If-None-Match` não ajudaria |
| 8 | `Content-Encoding` | confirmada | `gzip` nas 49 respostas |
| 9 | Tempo de download e timeout | inconclusiva (coleta pendente) | `station_status`: p50 0,6 s, p95 0,6 s, p99 0,9 s, máximo 0,9 s; 0 timeout em 41. Uma rede e 10 minutos não bastam para o p99 |
| 10 | Diferença entre o header `Date` e o relógio local | confirmada | o relógio local estava entre 0,9 s e 1,3 s adiantado. O intervalo junta as 49 respostas: o `Date` é truncado no segundo e foi carimbado entre `requestedAt` e `receivedAt`. Longe da tolerância de 30 s |
| 11 | Atraso de disponibilidade do arquivo | confirmada | limite superior (menor `requestedAt - last_updated`): 8,6 s. Limite inferior (maior `requestedAt` menos o `last_updated` da publicação seguinte, entre as respostas que ainda traziam a anterior): 10,3 s, em 13 de 41 respostas. A publicação seguinte é a próxima observada, porque o feed às vezes pula 15 s. Os limites se cruzam porque o atraso contado do `last_updated` varia com o `last_reported` (ver a nota); contado do `last_reported`, o atraso fica perto de zero. Idade no recebimento (`receivedAt - last_updated`): p50 10,9 s, p95 25,9 s, p99 26,0 s |
| 12 | Mudanças de contagem por hora | inconclusiva (coleta pendente) | 18 mudanças de estação em 600 s, perto de 108 por hora, logo depois da abertura |
| 13 | Licenciante para a atribuição NLOD | confirmada | "UIP Bauer Media Outdoor Norge AS" no `operator` de `system_information` e no rodapé das páginas open-data e realtime. O exemplo JSON da página realtime, de 2019, ainda traz "UIP Oslo Bysykkel AS" |

## Nota sobre `last_reported` e `last_updated`

Em cada publicação, todas as estações têm o mesmo `last_reported`, entre 0 e 14 s depois do `last_updated`. Essa diferença cresce de 1 a 2 s por publicação e volta a 0 a cada 11 a 13 publicações, no mesmo ponto em que o `last_updated` pula 30 s:

| `requestedAt` (UTC) | `last_updated` | `requestedAt - last_updated` | `last_reported - last_updated` |
|---|---|---|---|
| 03:29:25.153 | 03:29:00 | 25,2 s | 12 s |
| 03:29:40.167 | 03:29:15 | 25,2 s | 13 s |
| 03:29:55.180 | 03:29:30 | 25,2 s | 14 s |
| 03:30:10.183 | 03:30:00 | 10,2 s | 0 s |
| 03:30:25.189 | 03:30:15 | 10,2 s | 2 s |

A leitura que bate com os dados: o arquivo é gerado a cada 16 s, mais ou menos, o `last_updated` fica numa grade de 15 s e o `last_reported` marca a geração. Pelo relógio local, cada arquivo apareceu entre 1,0 s e 1,7 s depois do `last_reported` (o limite de cima vem do `receivedAt`). Descontado o adiantamento de 0,9 a 1,3 s do relógio, isso fica entre -0,3 s e +0,8 s no relógio do servidor: praticamente o próprio `last_reported`. Com a margem de 10 s, a coleta alterna entre trechos em que recebe a publicação nova (idade de 10 s) e trechos em que fica uma publicação atrás (idade de 25 s). Como o período de geração passa de 15 s, uma parte das respostas repete a publicação anterior: 3 de 41 nesta amostra. A proposta de margem está no ADR 0002.

## Recuperação da coleta interrompida

A gravação iniciada em 2026-10-09 às 09:38 UTC foi interrompida às 09:43, antes de fechar o gzip e escrever o manifesto. Foram recuperadas as 25 linhas JSON inteiras em `fixtures/recorded/recovered-20261009T093810Z.ndjson.gz`. O manifesto registra o hash do arquivo original, preservado em `data/recordings/`, e a recuperação; nenhum byte de uma linha incompleta foi aproveitado.

O trecho cobre 11:38:17 a 11:43:25 em Oslo: 25 respostas 200, sem falhas, 21 delas de `station_status`, com 267 estações válidas. O relatório offline mostrou 49 mudanças de contagem em 315 s, um `last_reported` por publicação e download p50 de 0,6 s, p95 de 0,9 s e p99 de 1,7 s. O prazo era 12 s; esse trecho não substitui uma amostra de 10 min com prazo de 30 s. O `compile-replay` gerou 21 eventos em memória, com 79 incidentes confirmados e nenhum resolvido: saída 3, sem artefato gravado.

## Gravação da demo

A coleta de 60 min terminou normalmente: 249 respostas 200, sem falha ou truncamento, das 22:25:27.929 às 23:25:15.533 UTC de 09/10/2026. Em Oslo, o intervalo vai de 00:25 a 01:25 de sábado, 10/10. Foram 240 respostas de `station_status`, 6 do cadastro e uma de cada feed de descoberta. O prazo foi o padrão de 12 s, com um único coletor.

O arquivo original ficou em `data/recordings/record-20261009T222700Z.ndjson.gz`. A cópia versionada recebeu o nome `record-20261009T222527Z.ndjson.gz`, alinhado ao primeiro `requestedAt`; só o campo `file` do manifesto mudou. O gzip mantém os bytes originais e o SHA256 `a5c03364ea0bac77e08ba9f76b31c0e755117d2ad588cc1bb9fa57acd1c62acb`. Gravação e manifesto somam 782.028 bytes, abaixo dos 5 MiB.

O [relatório offline](data/feed-20261009.txt) acrescenta estes resultados às premissas:

- GBFS 2.3, `ttl: 15`, gzip e 267 estações válidas, sem divergência entre ids do status e do cadastro.
- Um `last_reported` por resposta, de 0 a 14 s após `last_updated`; 216 publicações distintas em 240 respostas. O padrão continua compatível com `feed_generation`.
- Download de `station_status`: p50 634 ms, p95 716,4 ms, p99 1.079,3 ms e máximo 1.195 ms; nenhum timeout. Idade no recebimento: p50 14,8 s, p95 29,8 s e máximo 30,6 s.
- 479 mudanças de contagem em 3.585 s, cerca de 481 por hora nesse intervalo. A janela inclui 25 min depois do fechamento, portanto não representa o movimento diurno.
- Nenhum sinal de valet; ETag mudou em todas as 6 respostas do cadastro e nas 216 publicações distintas de status.

As premissas 6, 9 e 12 continuam inconclusivas fora desta janela e rede. A gravação atende ao aceite da demo, mas não substitui as amostras diurnas com prazo de 30 s.

O `compile-replay` passou, com 218 eventos, 40 incidentes confirmados e 2 resolvidos:

| Estação | Condição | Confirmação (evento / offset) | Resolução (evento / offset) |
|---|---|---|---|
| 537, St. Olavs gate | sem vagas | 69 / 1.125,368 s | 95 / 1.560,568 s |
| 412, Jakob kirke | sem bicicletas | 106 / 1.740,683 s | 124 / 2.040,832 s |

Os offsets contam do início do replay. O estado da fonte passou para `closed` às 23:00 UTC, exatamente 01:00 em Oslo. `fixtures/recorded/demo.yaml` escolhe esta gravação; `generate-artifacts` produziu `apps/web/public/replay/recorded/`, com 29.451 bytes em `events.ndjson.gz`, abaixo dos 2 MiB. Os hashes dos eventos e do catálogo estão no manifesto do artefato.

## Coleta pendente

Falta coletar em horário de movimento e de outras redes para as premissas 6, 9 e 12. Os comandos abaixo também servem para substituir a demo no futuro. Eles rodam da raiz do repositório, em bash (Git Bash ou WSL). No PowerShell, troque o `export` por `$env:CYCLEOPS_CLIENT_IDENTIFIER = "arthurjoanes-cycleops"`, escreva o carimbo do nome à mão e, no passo 3, passe os arquivos com `(Get-ChildItem fixtures/recorded/sample-*.ndjson.gz).FullName`, porque o PowerShell não expande o `*`. Se o `uv` não estiver no PATH, use `python -m uv`.

```bash
export CYCLEOPS_CLIENT_IDENTIFIER=arthurjoanes-cycleops
```

1. Amostras de 10 min em dia útil, perto de 07:30, 12:00 e 16:30 em Oslo, uma por horário:

```bash
uv run --project apps/backend record-feed --minutes 10 --deadline-seconds 30 --out fixtures/recorded/sample-$(date -u +%Y%m%dT%H%M%SZ).ndjson.gz
```

2. Gravação de 60 a 90 min no horário com mais mudanças. Ela fica em `data/recordings/`, fora do Git, e usa o prazo padrão:

```bash
uv run --project apps/backend record-feed --minutes 90 --out data/recordings/record-$(date -u +%Y%m%dT%H%M%SZ).ndjson.gz
```

3. Relatório sobre as gravações, sem rede:

```bash
uv run --project apps/backend inspect-feed --recording fixtures/recorded/sample-*.ndjson.gz
```

4. Aceite da gravação longa, sem rede. Ele passa quando algum incidente vai de `confirmed` a `resolved` dentro da gravação; sem isso o comando sai com código 3 e não escreve nada:

```bash
uv run --project apps/backend compile-replay --recording data/recordings/record-<UTC>.ndjson.gz --out data/replay-check
```

Se passar e tiver até 5 MB, copiar a gravação e o manifesto para `fixtures/recorded/`, criar `fixtures/recorded/demo.yaml` com `recording: <nome do arquivo>` e rodar `uv run --project apps/backend generate-artifacts`. Acima de 5 MB, o recorte ainda não existe.

Com os relatórios, atualizar esta tabela, o ADR 0002 e `docs/results/index.md`.
