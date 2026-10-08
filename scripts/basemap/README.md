# scripts/basemap

Gera a base do mapa da web a partir do OpenStreetMap: água, linha da costa, parques e vias principais, sem rótulos.

O script faz uma consulta ao Overpass (overpass-api.de) no bbox das estações gravadas em `fixtures/recorded/`, mais 1 km de cada lado, e converte o resultado com `ogr2ogr` num container GDAL fixado por digest. A query fica no próprio script.

## Como rodar

Da raiz do repositório, com Docker e curl:

```bash
scripts/basemap/generate.sh
```

No Windows, rode no Git Bash ou no WSL2. O script já define `MSYS_NO_PATHCONV=1`.

Roda só à mão. Nunca roda no build nem no CI.

## Saída

- `apps/web/public/basemap/basemap.geojson`: um FeatureCollection. Cada feature tem `kind` (`water`, `coast`, `park` ou `road`). As vias têm também `rank`: 1 para motorway e trunk, 2 para primary e secondary, 3 para tertiary.
- `apps/web/public/basemap/SOURCE.json`: fonte, query, data do download, licença (ODbL-1.0), bbox e imagem GDAL usada.

Os dois arquivos são versionados e não se editam à mão. O script falha se o GeoJSON passar de 2 MB com gzip.

O download bruto fica em `scripts/basemap/cache/`, fora do Git. Se já existe o cache da query atual, o script usa esse arquivo e não consulta o Overpass de novo.

Se o Overpass falhar, a alternativa é o extrato Østlandet da Geofabrik cortado com `osmium extract -b`, baixado fora do OneDrive. O comentário no topo do script descreve o passo.
