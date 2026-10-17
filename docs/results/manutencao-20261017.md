# Manutenção de 17/10/2026

Revisão local no Windows, Python 3.14.8 e Node 24.19.0, sobre `713c74d`. Código verificado em `6343194` (correção da API em `2a1497b`).

## Mudanças

Histórico e diagnóstico respondiam 500 ao subtrair a janela padrão de um `to` no início do ano 1 ou ao somar 24 horas a um `from` no último dia do ano 9999. As rotas agora compartilham a validação: duração por subtração, com 422 quando a janela padrão não cabe no calendário. Quatro casos de regressão falharam antes da correção e passaram depois.

Vite passou de 8.3.3 para 8.3.4 e Vitest de 5.0.3 para 5.0.5, com lockfile atualizado. As notas oficiais registram correções de build e HMR no [Vite](https://github.com/vitejs/vite/releases/tag/v8.3.4) e de testes e acesso a arquivos no [Vitest](https://github.com/vitest-dev/vitest/releases/tag/v5.0.5). Node 24 atende aos requisitos das duas versões.

## Comandos e resultados

Comandos da raiz; web executada em `apps/web`.

| Comando | Resultado |
|---|---|
| `python -m uv sync --project apps/backend --locked` | Ambiente sincronizado |
| `python -m uv run --project apps/backend pytest apps/backend` | 316 passaram; 58 integrações ignoradas sem PostgreSQL |
| `python -m uv run --project apps/backend ruff check apps/backend` | Sem erros |
| `python -m uv run --project apps/backend ruff format --check apps/backend` | 81 arquivos formatados |
| `python -m uv run --project apps/backend mypy --config-file apps/backend/pyproject.toml apps/backend/src apps/backend/tests` | 77 arquivos sem erros |
| `python -m uv run --project apps/backend generate-artifacts --check` | Artefatos atuais |
| `python -m uv run --project apps/backend python -m unittest discover -s scripts/tests` | 3 passaram |
| `python -m uv run --project apps/backend python scripts/check-recordings.py` | Sem erros |
| `python -m uv build --project apps/backend --out-dir <diretório-temporário>` | Wheel e sdist gerados |
| `corepack pnpm install --frozen-lockfile --offline` | Manifest e lockfile coerentes |
| `corepack pnpm generate:contracts` + diff de `packages/contracts` | Sem alterações |
| `corepack pnpm lint` e `corepack pnpm typecheck` | Sem erros |
| `corepack pnpm test` | 118 passaram |
| `corepack pnpm build:pages` e `corepack pnpm check:bundle` | Build gerado; entrada com 125.696 bytes gzip, teto de 127.026 |
| `corepack pnpm e2e` | 30 passaram no Chromium headless, incluindo 320, 390, 768 e 1440 px, teclado, replay e verificações axe |
| `corepack pnpm audit --json` | Nenhuma vulnerabilidade conhecida |
| `uv export --frozen --all-groups --no-emit-project` + `pip-audit==2.10.1 --disable-pip --require-hashes` | Nenhuma vulnerabilidade conhecida no lockfile Python |

## Alcance e pendências

A leitura manual cobriu limites e allowlist HTTP, prazo e tamanho de resposta, rate limit REST, cursor e filas SSE, papel somente leitura da API, transação de ingestão, validação dos artefatos e CSP. Isso não substitui uma execução do scanner Codex Security. A busca por formatos de chaves privadas e tokens não encontrou correspondências nos fontes pesquisados.

O daemon Linux do Docker estava indisponível (`dockerDesktopLinuxEngine` ausente). As 58 integrações PostgreSQL e o E2E Compose ficaram pendentes. O build manteve o aviso de chunks maiores que 500 kB; o limite separado do bundle inicial passou. A instalação do Chrome completo pelo CDN expirou, mas o Chromium headless já disponível executou os 30 E2E.

## Segunda rodada

Base `702fbdd`. A revisão aprofundou admissão e filas SSE, além da retenção do arquivo bruto e do banco. Uma tentativa de substituir o stream atual podia encerrá-lo antes de descobrir que faltava capacidade para aceitar a nova conexão. O novo teste reproduziu a falha; a admissão agora recusa a tentativa sem interromper o stream atual.

`python -m uv run --project apps/backend pytest apps/backend` terminou com 317 testes passando e 58 integrações ignoradas sem PostgreSQL. Ruff e formato passaram nos 81 arquivos; mypy passou nos 77 fontes. A seleção dos quatro casos de limites e substituição também passou. Retenção continua preservando o arquivo ativo e incidentes abertos; não houve mudança nessa política.

Os três workflows foram lidos e seus YAMLs analisados localmente. O Pages passou a aceitar somente execução manual na `main`; o CI do push não publica o site. A condição de repositório e branch continua no job de build. Nenhuma publicação foi executada nesta rodada.
