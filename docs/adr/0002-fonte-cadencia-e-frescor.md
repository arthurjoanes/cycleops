# ADR 0002: fonte, cadência, frescor, horário de serviço e `last_reported`

Status: Proposto, 2026-10-07. Os valores abaixo são os que estão em `policy/`; as propostas no fim esperam o aceite do Arthur.

## Contexto

A fonte é o feed GBFS 2.3 da Oslo Bysykkel em `https://gbfs.urbansharing.com/oslobysykkel.no/`: `gbfs.json`, `system_information`, `vehicle_types`, `station_information` e `station_status`. Licença NLOD 2.0, licenciante UIP Bauer Media Outdoor Norge AS. O operador pede o header `Client-Identifier` em toda requisição e não publica quota.

O sistema funciona das 05:00 à 01:00, horário de Oslo, todos os dias, e o feed não sinaliza o fechamento.

A fase 0 coletou 49 respostas em 2026-10-07, das 05:24 às 05:34 em Oslo. Os números estão em `docs/results/feed-validation.md`. Resumo do que pesa aqui:

- `ttl: 15` em todos os feeds e `last_updated` em degraus de 15 s, com um degrau de 30 s a cada 11 a 13 publicações;
- `last_reported` tem um valor só por publicação, de 0 a 14 s depois do `last_updated`, e muda para todas as estações em toda publicação;
- o arquivo é gerado a cada 16 s, mais ou menos, e fica disponível perto do `last_reported`;
- idade no recebimento: p50 10,9 s, p95 25,9 s, p99 26,0 s;
- download de `station_status`: p50 0,6 s, p99 0,9 s, sem timeout em 41 requisições, numa rede só;
- nenhum sinal de valet na amostra;
- ETag muda a cada publicação, também em `station_information`.

## Decisão

Coleta (bloco `collection` de `policy/provider-oslo.yaml`, módulo `cycleops.collection`):

- Só HTTPS para `gbfs.urbansharing.com`, com o caminho `/oslobysykkel.no/<feed>.json` de cada um dos 5 feeds. URL descoberta no `gbfs.json` fora disso é erro de contrato. Sem redirect, sem proxy do ambiente, com verificação de certificado.
- O bloqueio de IP privado depois da resolução de DNS não foi feito. O host é fixo e o certificado é verificado; o risco que sobra é um DNS adulterado apontar o host para a rede local, e o pedido continuaria sendo um GET sem credencial.
- `Client-Identifier` vem de `CYCLEOPS_CLIENT_IDENTIFIER`, sem default. Vazio ou com `<`/`>` (o placeholder do `.env.example`) impede o cliente de subir.
- Timeouts do HTTPX: connect 5 s, read 10 s, write 5 s, pool 5 s. Prazo total de 12 s por requisição, abaixo do piso de 15 s do intervalo. As CLIs aceitam `--deadline-seconds`.
- Corpo lido em streaming e cortado acima de 2 MiB decodificados.
- Intervalo de `station_status`: `max(15, ttl)`. O próximo GET sai em `max(início anterior + intervalo, last_updated + ttl + 10)`. Uma requisição em voo por feed.
- `station_information` a cada 600 s; `gbfs.json`, `system_information` e `vehicle_types` na partida e a cada 3600 s.
- Erro de transporte (timeout, rede, corpo truncado, 5xx, 429): 3 seguidos em `station_status` abrem o breaker por 60 s, depois uma tentativa. O 429 respeita `Retry-After`.
- Erro de contrato (outros 4xx, redirect, corpo acima de 2 MiB, JSON inválido, versão diferente de 2.3, schema): quarentena do feed, com uma sonda a cada 300 s e log de erro com fingerprint. Uma resposta válida encerra a quarentena. O primeiro erro de contrato de um feed busca o `gbfs.json` de novo.
- 404: busca o `gbfs.json` e tenta de novo uma vez; um segundo 404 seguido põe o feed em quarentena.

Frescor e horário (`policy/priority-policy.yaml` e `policy/provider-oslo.yaml`):

- `sourceLateAfterSeconds` 40 e `sourceStaleAfterSeconds` 60. Ficam acima do p95 e do p99 da idade no recebimento medidos (25,9 s e 26,0 s).
- Horário de serviço de 05:00 a 01:00, Europe/Oslo, conferido no FAQ do operador em 2026-10-06.
- `stationReportSemantics: feed_generation`. O `last_reported` é guardado e mostrado como "gerado pelo provedor", sem entrar em regra.
- Sem sinal de valet: `num_docks_available` ausente continua sendo devolução desconhecida, e a falta de vaga vale como "sem vagas físicas segundo o feed".

## Propostas para aceite

1. Manter `feed_generation`. A amostra confirma o comportamento e não há mudança de arquivo.
2. Não adotar sinal de valet por enquanto. Repetir a checagem numa amostra de dia, em dia de valet.
3. Margem de publicação: ficar com 10 s até a gravação longa. Alternativa medida: com 15 s, a coleta pediria o arquivo em `last_updated + 30`, quando a publicação seguinte já saiu (o atraso observado vai até 14 s). Pela amostra, a idade no recebimento ficaria perto de 15 s o tempo todo, em vez de alternar entre 10 s e 25 s, e o p95 cairia de 26 s para perto de 15 s. É estimativa; confirmar na gravação longa. Isso pede subir `sourceLateAfterSeconds` para 45, para seguir cobrindo um ciclo de coleta (15 + 15 + 12 = 42 s).
4. Timeouts e prazo: ficam como estão. O p99 de 0,9 s não diz nada sobre a rede de dia nem sobre outras redes. Se as amostras pendentes mostrarem p99 acima de 15 s, decidir entre subir o piso e o prazo ou aceitar a taxa de timeout medida.

## Alternativas

- `If-None-Match` com o ETag: não reduz nada, porque o ETag muda a cada publicação.
- Cadência fixa de 10 s, como diz a página do operador: geraria pedidos repetidos sem dado novo, e o `ttl` publicado é 15.
- Semântica `per_station`: o feed não relata por estação; ela marcaria todas as estações como relatadas a cada publicação.

## Consequências

- A idade da fonte é medida a partir do `last_updated`, que chega a ficar 14 s antes da geração real do arquivo. A idade mostrada é um pouco maior que a real.
- Parte das respostas repete a publicação anterior (3 de 41 na amostra) e vira `duplicate`.
- Um feed em quarentena faz no máximo uma requisição a cada 5 minutos.

## Risco

- A amostra tem 10 minutos, logo depois da abertura do sistema, e vem de uma rede só.
- Se o operador mudar para GBFS 3.x, todo feed vira erro de contrato e entra em quarentena até existir um adaptador novo.
- O padrão de geração a cada 16 s pode mudar sem aviso; a cadência pela fórmula se ajusta, mas a margem pode precisar de revisão.

## Quando revisar

- Com as amostras de dia e a gravação de 60 a 90 minutos.
- Se o p99 do download passar de 15 s ou aparecerem timeouts.
- Se o feed mudar de versão, de `ttl` ou de comportamento do `last_reported`.
