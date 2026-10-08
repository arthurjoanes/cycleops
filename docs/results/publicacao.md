# Publicação

Passos para pôr o repositório e a demo no GitHub. Os workflows já estão em `.github/workflows/`:

- `ci.yml`: backend (ruff, mypy, `migrate` a partir do zero, pytest com integração num PostgreSQL de service container, `generate-artifacts --check`), web (lint, typecheck, Vitest, build do Pages, `check:bundle`, Playwright sob `/cycleops/`), Compose sintético sem internet (smoke, saída do worker bloqueada e o E2E do Compose), checagem de texto e gitleaks. Roda em PR e em push na `main`.
- `pages.yml`: build e deploy do Pages. Roda depois de um `ci` verde em push na `main`, ou à mão.
- `feed-check.yml`: `inspect-feed` com 5 requisições, só à mão e só em `arthurjoanes/cycleops`.

Nenhum deles precisa de secret.

## Checklist

Nada abaixo foi executado ainda. A ordem importa só onde um passo depende do anterior.

### Dados da demo

- [ ] Rodar as gravações pendentes (`feed-validation.md`, "Coleta pendente") e, sobre a gravação longa, o aceite e o `compile-replay`. Sem gravação aceita, a demo abre na simulação. Duas formas:
  - pelas CLIs, com `CYCLEOPS_CLIENT_IDENTIFIER` e sem o live no ar (os comandos estão no `feed-validation.md`);
  - pelo live no Compose: deixar o `cycleops-live` rodando o tempo da gravação e exportar o intervalo do arquivo bruto, sem requisição nova:

    ```bash
    MSYS_NO_PATHCONV=1 docker compose -p cycleops-live --env-file .env.live run --rm -v ./export:/export worker record-feed --from-archive --from <utc> --to <utc> --out /export/<nome>.ndjson.gz
    ```

    O `MSYS_NO_PATHCONV=1` é para o Git Bash, que senão troca o `/export/...` por um caminho do Windows; no WSL ele não muda nada.

- [ ] Se a gravação passar no aceite e tiver até 5 MB: copiar a gravação e o manifesto para `fixtures/recorded/`, criar `fixtures/recorded/demo.yaml` com `recording: <arquivo>`, rodar `generate-artifacts`, atualizar `docs/results/feed-validation.md`, o ADR 0002 e `docs/results/index.md`, e commitar. Acima de 5 MB, o recorte ainda não existe. Esses passos também podem ficar com o agente, numa tarefa só para a gravação.

### Decisões

- [ ] Aceitar ou não os ADRs 0002, 0003, 0004 e 0005 (todos Propostos). Trocar o status para Aceito, com a data, nos que forem aceitos.
- [ ] Decidir o gatilho do ADR 0005: com a CPU 4× mais lenta, "Todas as estações" com 1.000 estações passou do limite de long task. Para Oslo (267 estações), manter a lista completa ou virtualizar depois do MVP.

### GitHub

- [ ] Criar o repositório público `arthurjoanes/cycleops`, vazio (sem README, licença ou `.gitignore` do GitHub).
- [ ] Em Settings > Actions > General, deixar "Workflow permissions" em "Read repository contents and packages permissions". Os workflows pedem só o que usam.
- [ ] Em Settings > Pages, "Build and deployment", Source: GitHub Actions.
- [ ] Em Settings > Security, ligar "Private vulnerability reporting" (o `SECURITY.md` aponta para ele) e os alertas do Dependabot.
- [ ] Push da `main`:

  ```bash
  git remote add origin https://github.com/arthurjoanes/cycleops.git
  git push -u origin main
  ```

- [ ] Ver o `ci` rodar em Actions: backend com integração, web, Compose sem internet, checagem de texto e gitleaks. Com ele verde, o `pages` roda sozinho; o primeiro deploy cria o environment `github-pages`.
- [ ] Abrir https://arthurjoanes.github.io/cycleops/ e conferir: o replay carrega (no DevTools, `events.ndjson.gz` chega como `application/gzip`, sem `Content-Encoding`), o mapa aparece com as estações e o console não mostra violação de CSP.
- [ ] Pôr no `README.md` e no `README.en.md` o link da demo e o badge de CI, depois do primeiro CI verde:

  ```markdown
  [![ci](https://github.com/arthurjoanes/cycleops/actions/workflows/ci.yml/badge.svg)](https://github.com/arthurjoanes/cycleops/actions/workflows/ci.yml)

  Demo: https://arthurjoanes.github.io/cycleops/
  ```

- [ ] Preencher o About: "Bike-share station dashboard for Oslo built on open GBFS data, with a priority queue and replay of recorded feeds." Tópicos: `react`, `typescript`, `fastapi`, `real-time`, `sse`, `gbfs`, `maplibre`, `data-visualization`, `postgresql`.
- [ ] Rodar uma vez o `feed-check` em Actions > feed-check > Run workflow e conferir que fez 5 requisições.
- [ ] Aceitar a atualização do Playwright para 1.64 quando o Dependabot abrir: ele ficou em 1.63.0 porque o pnpm recusa pacote com menos de um dia de publicado.

### Testes que pedem uma pessoa

- [ ] Preencher `docs/results/a11y-manual.md`, item a item.
- [ ] Opcional: deixar o live rodando na virada da 01:00 e das 05:00 em Oslo e anotar no `docs/results/live-manual.md` o fechamento, a abertura e uma resolução de incidente, que o teste curto não pegou.
