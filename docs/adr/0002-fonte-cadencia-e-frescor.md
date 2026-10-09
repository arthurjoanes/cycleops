# ADR 0002: fonte, cadência, frescor, horário de serviço e `last_reported`

Status: Proposto, 2026-10-07, revisto em 2026-10-09 com a gravação de uma hora. Os valores abaixo são os que estão em `policy/`; as propostas no fim esperam o aceite do Arthur.

## Contexto

A fonte é o feed GBFS 2.3 do Oslo Bysykkel em `https://gbfs.urbansharing.com/oslobysykkel.no/`: `gbfs.json`, `system_information`, `vehicle_types`, `station_information` e `station_status`. Licença NLOD 2.0, licenciante UIP Bauer Media Outdoor Norge AS. O operador pede o header `Client-Identifier` em toda requisição e não publica quota.

O sistema funciona das 05:00 à 01:00, horário de Oslo, todos os dias, e o feed não sinaliza o fechamento.

A primeira coleta reuniu 49 respostas em 2026-10-07, das 05:24 às 05:34 em Oslo. Os números estão em `docs/results/feed-validation.md`. Resumo do que pesa aqui:

- `ttl: 15` em todos os feeds e `last_updated` em degraus de 15 s, com um degrau de 30 s a cada 11 a 13 publicações;
- `last_reported` tem um valor só por publicação, de 0 a 14 s depois do `last_updated`, e muda para todas as estações em toda publicação;
- o arquivo é gerado a cada 16 s, mais ou menos, e fica disponível praticamente no instante do `last_reported`;
- idade no recebimento: p50 10,9 s, p95 25,9 s, p99 26,0 s;
- download de `station_status`: p50 0,6 s, p99 0,9 s, sem timeout em 41 requisições, numa rede só;
- nenhum sinal de valet na amostra;
- ETag muda a cada publicação, também em `station_information`.

O teste manual do live em 2026-10-08, das 11:08 às 11:16 em Oslo, confirmou o mesmo padrão com o worker (`docs/results/live-manual.md`): 32 requisições de `station_status` a cada 15 s, 2 delas `duplicate`, idade da publicação entre 10 e 25 s na tela, nenhum timeout, warning ou erro.

A gravação `fixtures/recorded/record-20261009T222527Z.ndjson.gz` cobre 2026-10-09 das 22:25:27.929 às 23:25:15.533 UTC, de 00:25 a 01:25 de 2026-10-10 em Oslo. Foram 249 respostas 200, sem falhas nem truncamentos, com prazo total de 12 s. O relatório em `docs/results/data/feed-20261009.txt` saiu de `inspect-feed --recording`, sem requisições novas:

- GBFS 2.3, `ttl: 15` e gzip nas 249 respostas. Das 240 respostas de `station_status`, 216 são publicações distintas; 24 repetem a anterior. Entre publicações, 191 degraus de 15 s e 24 de 30 s no `last_updated`.
- Um `last_reported` por resposta, entre 0 e 14 s depois do `last_updated`. Ele avançou tanto nos 479 pares estação-publicação com mudança de contagem como nos 56.926 sem mudança. O padrão continua sendo `feed_generation`.
- Idade no recebimento: p50 14,8 s, p95 29,8 s, p99 30,4 s e máximo 30,6 s. Download de `station_status`: p50 0,6 s, p95 0,7 s, p99 1,1 s e máximo 1,2 s; nenhum timeout em 240 requisições.
- Nenhum sinal de valet nas 240 respostas. A última publicação tem 267 estações válidas, os mesmos ids do cadastro, e nenhuma contagem acima da capacidade. As 6 respostas do cadastro têm ETags distintos.
- Foram 479 mudanças de contagem em 3.585 s, cerca de 481 por hora nessa janela. A coleta atravessou o fechamento à 01:00; o motor passou a `closed` às 23:00 UTC pela regra de horário. O feed continuou publicando depois disso.

O replay dessa gravação passou pelo aceite: 218 eventos, 40 incidentes confirmados e 2 resolvidos. A janela da madrugada, em uma rede só e com prazo de 12 s, não substitui as amostras de 10 minutos com prazo de 30 s perto de 07:30, 12:00 e 16:30 em Oslo, ainda pendentes.

## Decisão

Coleta (bloco `collection` de `policy/provider-oslo.yaml`, módulo `cycleops.collection`):

- Só HTTPS para `gbfs.urbansharing.com`, com o caminho `/oslobysykkel.no/<feed>.json` de cada um dos 5 feeds. URL descoberta no `gbfs.json` fora disso é erro de contrato. Sem redirect, sem proxy do ambiente, com verificação de certificado.
- O bloqueio de IP privado depois da resolução de DNS não foi feito. O host é fixo e o certificado é verificado; o risco que sobra é um DNS adulterado apontar o host para a rede local, e o pedido continuaria sendo um GET sem credencial.
- `Client-Identifier` vem de `CYCLEOPS_CLIENT_IDENTIFIER`, sem default. Vazio ou com `<`/`>` (o placeholder do `.env.example`) impede o cliente de subir.
- Timeouts do HTTPX: connect 5 s, read 10 s, write 5 s, pool 5 s. Prazo total de 12 s por requisição, abaixo do piso de 15 s do intervalo. As CLIs aceitam `--deadline-seconds`.
- Corpo lido em streaming e cortado acima de 2 MiB decodificados.
- Intervalo de `station_status`: `max(15, ttl)`. O próximo GET sai em `max(início anterior + intervalo, last_updated + ttl + 10)`. Um `last_updated` no futuro entra na conta limitado ao instante devido pelo intervalo, para não adiar a coleta. Uma requisição em voo por feed.
- `station_information` a cada 600 s; `gbfs.json`, `system_information` e `vehicle_types` na partida e a cada 3600 s.
- Erro de transporte (timeout, rede, corpo truncado, 5xx, 429): 3 seguidos em `station_status` abrem o breaker por 60 s, depois uma tentativa. O 429 respeita `Retry-After`.
- Erro de contrato (outros 4xx, redirect, corpo acima de 2 MiB, JSON inválido, versão diferente de 2.3, schema): quarentena do feed, com uma sonda a cada 300 s e log de erro com fingerprint. Uma resposta válida encerra a quarentena. O primeiro erro de contrato de um feed busca o `gbfs.json` de novo.
- 404: busca o `gbfs.json` e tenta de novo uma vez; um segundo 404 seguido põe o feed em quarentena.
- Uma coleta por deployment. O worker só coleta com o advisory lock do coletor no PostgreSQL. `inspect-feed` e `record-feed` com `DATABASE_URL` tomam o mesmo lock, recusam rodar se um worker lidera e param se perdem o lock no meio. Com o live no ar, a gravação sai do arquivo bruto do worker (`record-feed --from-archive`), sem requisição nova.
- Breaker, quarentena e a última tentativa de cada feed ficam no banco (`stream_heads` e `feed_publications`). Depois de um restart, a requisição seguinte de cada feed sai na cadência normal contada da tentativa anterior, e um `Retry-After` pendente continua valendo. No teste do live, o worker reiniciou às 09:10:17 UTC e o `station_status` seguinte saiu às 09:10:25, 15 s depois do anterior, sem buscar de novo os feeds de descoberta.
- Com a ingestão suspensa pela cota do banco, o coletor não faz requisição; a fonte fica `stale` com `lastOutcome: db_quota_exceeded` até a purga trazer o banco abaixo da cota.
- Entrada fora de faixa nunca derruba a coleta: `last_updated` fora de 1970 a 2100 e `ttl` acima de 86400 s são erro de contrato. Um `last_reported` 0 ou negativo conta como ausente, e acima de 2100 (por exemplo em milissegundos) conta como no futuro: a estação fica `unknown` ou `suspect` e não é descartada.

Frescor e horário (`policy/priority-policy.yaml` e `policy/provider-oslo.yaml`):

- `sourceLateAfterSeconds` 40 e `sourceStaleAfterSeconds` 60. Ficam acima do p95 e do p99 da idade no recebimento nas duas gravações: 25,9 s e 26,0 s na amostra inicial; 29,8 s e 30,4 s na gravação de uma hora. Os valores permanecem iguais.
- A fonte vencida por idade começa no instante em que a idade passou de 60 s, e esse instante vai no `FeedHealth` (`stateSince`). Com o worker parado, a API aplica a mesma regra sobre a última publicação e o último tick gravados, e o worker, ao voltar, mantém o mesmo início.
- Horário de serviço de 05:00 a 01:00, Europe/Oslo, conferido no FAQ do operador em 2026-10-06.
- `stationReportSemantics: feed_generation`. O `last_reported` é guardado e mostrado como "gerado pelo provedor", sem entrar em regra.
- Sem sinal de valet: `num_docks_available` ausente continua sendo devolução desconhecida, e a falta de vaga conta só as vagas físicas informadas pelo feed.

## Propostas para aceite

1. Manter `feed_generation`. A amostra inicial e a gravação de uma hora mostram o mesmo comportamento; não há mudança de arquivo.
2. Não adotar sinal de valet por enquanto. Ele não apareceu nas duas gravações. Repetir a checagem numa amostra de dia, em dia de valet.
3. Manter a margem de publicação em 10 s. Simulei a regra de agendamento sobre as publicações da amostra inicial, com cada arquivo visível de 1,0 s a 1,7 s depois do `last_reported` pelo relógio local; o resultado é o mesmo em toda essa faixa. Com 10 s a simulação reproduz o medido (p50 10,6 s e p95 25,6 s de idade no recebimento, contra 10,9 s e 25,9 s, e as mesmas 3 repetições). Com 15 s ela piora: p50 15,6 s, p95 30,6 s e as mesmas 3 repetições, que caem nos degraus de 30 s do `last_updated`. Essa comparação ainda usa a amostra inicial; falta repeti-la sobre a gravação de uma hora e as amostras de dia.
4. Timeouts e prazo: ficam como estão. O p99 foi de 0,9 s na amostra inicial e 1,1 s na gravação de uma hora, sem timeout. A coleta mais recente usou prazo de 12 s e a mesma rede; não mede a rede de dia nem outras redes. Se as amostras pendentes, com prazo de 30 s, mostrarem p99 acima de 15 s, decidir entre subir o piso e o prazo ou aceitar a taxa de timeout medida.

## Alternativas

- `If-None-Match` com o ETag: não reduz nada, porque o ETag muda a cada publicação.
- Cadência fixa de 10 s, como diz a página do operador: geraria pedidos repetidos sem dado novo, e o `ttl` publicado é 15.
- Semântica `per_station`: o feed não relata por estação; ela marcaria todas as estações como relatadas a cada publicação.

## Consequências

- A idade da fonte é medida a partir do `last_updated`, que chega a ficar 14 s antes da geração real do arquivo. A idade mostrada é um pouco maior que a real.
- Parte das respostas repete a publicação anterior (3 de 41 na amostra inicial e 24 de 240 na gravação de uma hora) e vira `duplicate`.
- Um feed em quarentena faz no máximo uma requisição a cada 5 minutos.

## Risco

- A amostra inicial tem 10 minutos logo depois da abertura; o teste do live acrescentou 8 minutos no fim da manhã. A gravação de uma hora cobre a madrugada e inclui cerca de 25 minutos depois do fechamento. Todas vieram de uma rede só.
- As amostras de 10 minutos perto de 07:30, 12:00 e 16:30 em Oslo continuam pendentes, assim como a checagem de valet durante o dia. O prazo de 12 s da gravação longa limita a medição de respostas lentas.
- Se o operador mudar para GBFS 3.x, todo feed vira erro de contrato e entra em quarentena até existir um adaptador novo.
- O padrão de geração a cada 16 s pode mudar sem aviso; a cadência pela fórmula se ajusta, mas a margem pode precisar de revisão.

## Quando revisar

- Com as amostras de dia e a comparação de margens sobre a gravação de uma hora.
- Se o p99 do download passar de 15 s ou aparecerem timeouts.
- Se o feed mudar de versão, de `ttl` ou de comportamento do `last_reported`.
