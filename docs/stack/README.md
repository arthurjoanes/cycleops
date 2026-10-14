# Ícones da stack

Os arquivos usam um quadro de 72 × 72 unidades, exibido em 64 × 64 px nos READMEs em português e inglês. Cada SVG identifica a fonte do símbolo em um comentário.

Python, FastAPI, PostgreSQL, SQLAlchemy, TypeScript, React, Zustand, Vite e Docker vêm do [Devicon](https://github.com/devicons/devicon/tree/7330accdbc47e2dc0c19789a48533c4a3c50fe58), revisão `7330accdbc47e2dc0c19789a48533c4a3c50fe58`, sob [licença MIT](LICENSE.devicon). Os símbolos mantêm as cores e proporções da fonte.

MapLibre, Pydantic, Caddy e Ajv vêm do [Simple Icons 14.15.0](https://github.com/simple-icons/simple-icons/tree/5664507081ffb65b5a2a0ed2bf2ee2b3b327cb4e), revisão `5664507081ffb65b5a2a0ed2bf2ee2b3b327cb4e`, sob [CC0-1.0](LICENSE.simple-icons). Os símbolos usam as cores registradas no catálogo. O quadro e os rótulos seguem a [licença MIT do projeto](../../LICENSE).

| Símbolo | Arquivo no catálogo | Fonte original indicada no catálogo |
|---|---|---|
| Pydantic | [pydantic.svg](https://github.com/simple-icons/simple-icons/blob/5664507081ffb65b5a2a0ed2bf2ee2b3b327cb4e/icons/pydantic.svg) | [Pydantic](https://github.com/pydantic/pydantic/blob/94c748001a32992a587694b999fb1f3d2f1fc1fe/docs/logo-white.svg) |
| Caddy | [caddy.svg](https://github.com/simple-icons/simple-icons/blob/5664507081ffb65b5a2a0ed2bf2ee2b3b327cb4e/icons/caddy.svg) | [Caddy](https://caddyserver.com) |
| Ajv | [ajv.svg](https://github.com/simple-icons/simple-icons/blob/5664507081ffb65b5a2a0ed2bf2ee2b3b327cb4e/icons/ajv.svg) | [Ajv, fonte sob MIT](https://github.com/ajv-validator/ajv/blob/95b15b683dfb60f63c5129b0426629b968d53af8/docs/.vuepress/public/img/ajv.svg) |
| MapLibre | [maplibre.svg](https://github.com/simple-icons/simple-icons/blob/5664507081ffb65b5a2a0ed2bf2ee2b3b327cb4e/icons/maplibre.svg) | [MapLibre](https://github.com/maplibre/maplibre-gl-js-docs/blob/e916a4cdd671890126f88b26b2b16c04220dc4b0/docs/pages/assets/favicon/maplibregl-favicon.svg) |

Pydantic define os contratos do backend; SQLAlchemy participa do acesso ao banco. Zustand mantém o estado da interface. Ajv gera os validadores que rodam no navegador. As dependências estão em [pyproject.toml](../../apps/backend/pyproject.toml) e [package.json](../../apps/web/package.json). O [Caddy](../../infra/caddy/Caddyfile) serve a interface e encaminha as chamadas para a API.

Os avisos das demais dependências estão em [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md).
