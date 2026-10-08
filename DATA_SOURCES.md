# Fontes de dados

## Oslo Bysykkel (GBFS 2.3)

Contém dados sob a Norwegian Licence for Open Government Data (NLOD) 2.0 (https://data.norge.no/nlod/en/2.0), disponibilizados por Oslo Bysykkel (UIP Bauer Media Outdoor Norge AS), em https://oslobysykkel.no/en/open-data. Dados normalizados pelo CycleOps; incidentes e prioridades são classificações do CycleOps.

O mesmo texto aparece no rodapé da interface, nos três modos. O CycleOps não é serviço oficial do Oslo Bysykkel e não tem parceria com o operador.

Feeds usados:

```text
https://gbfs.urbansharing.com/oslobysykkel.no/gbfs.json
https://gbfs.urbansharing.com/oslobysykkel.no/system_information.json
https://gbfs.urbansharing.com/oslobysykkel.no/vehicle_types.json
https://gbfs.urbansharing.com/oslobysykkel.no/station_information.json
https://gbfs.urbansharing.com/oslobysykkel.no/station_status.json
```

Como o CycleOps usa o feed:

- toda requisição manda `Client-Identifier`, lido de `CYCLEOPS_CLIENT_IDENTIFIER`, como o operador pede;
- uma requisição em voo por feed, na cadência do TTL, sem teste de carga contra o operador;
- as gravações em `fixtures/recorded/` são respostas do feed, uma linha por requisição, com o `NOTICE` da NLOD. O campo `station_area` é removido na gravação;
- os resultados da coleta estão em `docs/results/feed-validation.md`.

Sem vagas físicas segundo o feed; o ∞ do app não aparece no feed. Nas coletas feitas até agora nenhuma estação veio sem `num_docks_available` ou com bicicletas mais vagas acima da capacidade, e o cadastro não traz `is_valet_station`.

O operador não publica SLA nem quota. O feed não sinaliza o fechamento noturno (01:00 às 05:00); o horário vem do FAQ do operador e está em `policy/provider-oslo.yaml`.

## OpenStreetMap

A base do mapa (`apps/web/public/basemap/basemap.geojson`) tem água, linha da costa, parques e vias principais de Oslo, sem rótulos. Vem do OpenStreetMap pela Overpass API, convertida com GDAL por `scripts/basemap/generate.sh`.

© OpenStreetMap contributors, sob a Open Database License (ODbL) 1.0 (https://opendatacommons.org/licenses/odbl/1-0/). A consulta, a data do download e o bbox estão em `apps/web/public/basemap/SOURCE.json`.

A base é simplificada e não serve para navegação.

## Dados sintéticos

O modo Simulação usa estações e contagens geradas com seed (`fixtures/synthetic/demo.yaml`). Os nomes começam com "Estação sintética" e a interface mostra a faixa "Simulação" o tempo todo. Não são dados do Oslo Bysykkel.
