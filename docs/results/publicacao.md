# Publicação

Passos para pôr o repositório e a demo no GitHub. Os workflows já estão em `.github/workflows/`:

- `ci.yml`: backend (ruff, mypy, pytest, `generate-artifacts --check`), web (lint, typecheck, Vitest, build do Pages, `check:bundle`, Playwright sob `/cycleops/`), checagem de texto e gitleaks. Roda em PR e em push na `main`.
- `pages.yml`: build e deploy do Pages. Roda depois de um `ci` verde em push na `main`, ou à mão.
- `feed-check.yml`: `inspect-feed` com 5 requisições, só à mão e só em `arthurjoanes/cycleops`.

Nenhum deles precisa de secret.

## Checklist

Nada abaixo foi executado ainda.

- [ ] Rodar as gravações pendentes (`feed-validation.md`, "Coleta pendente") e, sobre a gravação longa, o aceite e o `compile-replay`. Sem gravação aceita, a demo abre na simulação.
- [ ] Criar o repositório público `arthurjoanes/cycleops`, vazio (sem README, licença ou `.gitignore` do GitHub).
- [ ] Em Settings > Actions > General, deixar "Workflow permissions" em "Read repository contents and packages permissions". Os workflows pedem só o que usam.
- [ ] Em Settings > Pages, "Build and deployment", Source: GitHub Actions.
- [ ] Push da `main`:

  ```bash
  git remote add origin https://github.com/arthurjoanes/cycleops.git
  git push -u origin main
  ```

- [ ] Ver o `ci` rodar em Actions. Com ele verde, o `pages` roda sozinho; o primeiro deploy cria o environment `github-pages`.
- [ ] Abrir https://arthurjoanes.github.io/cycleops/ e conferir: o replay carrega (no DevTools, `events.ndjson.gz` chega como `application/gzip`, sem `Content-Encoding`), o mapa aparece com as estações e o console não mostra violação de CSP.
- [ ] Pôr no README o link da demo e o badge de CI, depois do primeiro CI verde:

  ```markdown
  [![ci](https://github.com/arthurjoanes/cycleops/actions/workflows/ci.yml/badge.svg)](https://github.com/arthurjoanes/cycleops/actions/workflows/ci.yml)

  Demo: https://arthurjoanes.github.io/cycleops/
  ```

- [ ] Preencher o About: "Bike-share station dashboard for Oslo built on open GBFS data, with a priority queue and replay of recorded feeds." Tópicos: `react`, `typescript`, `fastapi`, `real-time`, `sse`, `gbfs`, `maplibre`, `data-visualization`, `postgresql`.
- [ ] Em Settings > Security, ligar "Private vulnerability reporting" (o `SECURITY.md` aponta para ele) e os alertas do Dependabot.
- [ ] Preencher `docs/results/a11y-manual.md` (ainda não existe).
- [ ] Rodar uma vez o `feed-check` em Actions > feed-check > Run workflow.
