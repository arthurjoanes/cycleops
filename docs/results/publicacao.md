# Publicação

[Repositório público](https://github.com/arthurjoanes/cycleops) · [Demo no navegador](https://arthurjoanes.github.io/cycleops/).

Os workflows estão em `.github/workflows/`:

- `ci.yml`: backend (ruff, mypy, `migrate` a partir do zero, pytest com integração num PostgreSQL de service container, `generate-artifacts --check`), web (lint, typecheck, Vitest, build do Pages, `check:bundle`, Playwright sob `/cycleops/`), Compose sintético sem internet (smoke, saída do worker bloqueada e o E2E do Compose), checagem de texto e gitleaks. Roda em PR e em push na `main`.
- `pages.yml`: build e deploy do Pages, somente por execução manual na `main`. Confira o `ci` do commit escolhido antes de publicar.
- `feed-check.yml`: `inspect-feed` com 5 requisições, só à mão e só em `arthurjoanes/cycleops`.

Nenhum deles precisa de secret.

## Publicação de 2026-10-10

O commit `185e5af` passou no [CI 38007237961](https://github.com/arthurjoanes/cycleops/actions/runs/38007237961) e foi publicado pelo [Pages 38007708719](https://github.com/arthurjoanes/cycleops/actions/runs/38007708719). A data está em UTC; no horário de Brasília, a publicação ocorreu em 09/10.

| Verificação no GitHub | Resultado |
|---|---|
| Backend | 354 testes com PostgreSQL, Ruff, mypy, migrações e artefatos gerados passaram |
| Web | 69 testes unitários, 14 E2E, lint e TypeScript passaram; bundle inicial de 119.270 bytes gzip |
| Compose sem internet | Smoke e 11 E2E passaram; a medição de latência ficou fora desta execução porque exige `CYCLEOPS_MEASURE=1` |
| Gitleaks | 26 commits examinados, nenhum segredo encontrado |

As primeiras execuções identificaram dois ajustes nos testes: a guarda de plataforma de uma constante exclusiva do Windows (`4474e64`) e o uso de um horário de serviço sempre aberto no teste de falhas do worker (`dca5f8f`). O teste do worker e o de shutdown também passaram localmente, juntos, durante o fechamento real de Oslo: dois testes em 42,38 s.

A [verificação do endereço público](data/public-pages-20261010.json) foi executada às 00:08 UTC com Chromium 153.0.8010.12, em 1440×900 e 320×900. O roteiro abriu a demo, conferiu modo e data, pausou, saltou para um incidente e abriu o mapa. Logo e favicon chegaram como SVG; o replay, como `application/gzip`, sem `Content-Encoding`. Não houve overflow horizontal, erro de JavaScript ou CSP, resposta HTTP com erro, chamada à API ou requisição a outra origem.

## Checklist

Os itens marcados já foram executados no ambiente local ou no GitHub. Os testes que pedem uma pessoa continuam pendentes. A ordem importa só onde um passo depende do anterior.

### Dados da demo

- [x] Gravação longa da demo em `fixtures/recorded/`, com manifesto e SHA256 conferido.
- [x] Aceite com um incidente confirmado e resolvido na gravação real: falta de vagas em St. Olavs gate (estação 537). Resultado em [feed-validation.md](feed-validation.md).
- [x] Artefatos do replay gravado gerados a partir de `fixtures/recorded/demo.yaml`, contratos conferidos e resultados registrados em [index.md](index.md).
- [ ] Rodar as amostras diurnas adicionais e, quando possível, de outras redes para avaliar as premissas de valet, download e mudanças de contagem. Elas estão em "Coleta pendente", no [feed-validation.md](feed-validation.md); o aceite da demo já foi atendido.

Para novas gravações, use as CLIs com `CYCLEOPS_CLIENT_IDENTIFIER` e sem o live no ar (comandos no `feed-validation.md`). Com o live no Compose, exporte o intervalo do arquivo bruto, sem requisição nova:

```bash
MSYS_NO_PATHCONV=1 docker compose -p cycleops-live --env-file .env.live run --rm -v ./export:/export worker record-feed --from-archive --from <utc> --to <utc> --out /export/<nome>.ndjson.gz
```

O `MSYS_NO_PATHCONV=1` é para o Git Bash, que senão troca o `/export/...` por um caminho do Windows; no WSL ele não muda nada.

Uma gravação futura que substitua a demo precisa passar no aceite e caber no limite de 5 MiB, somando `.ndjson.gz` e manifesto. Acima do limite, faça um recorte que comece pelo menos 5 min antes da confirmação e termine depois da resolução, registre o hash da gravação original no manifesto e repita o aceite. Depois atualize `fixtures/recorded/demo.yaml`, rode `generate-artifacts` e registre o resultado em `feed-validation.md`, no ADR 0002 e em `index.md`.

### Decisões

- [ ] Aceitar ou não os ADRs 0002, 0003, 0004 e 0005 (todos Propostos). Trocar o status para Aceito, com a data, nos que forem aceitos.
- [ ] Decidir o gatilho do ADR 0005: com a CPU 4× mais lenta, "Todas as estações" com 1.000 estações passou do limite de long task. Para Oslo (267 estações), manter a lista completa ou virtualizar depois do MVP.

### GitHub

- [x] Criar o repositório público `arthurjoanes/cycleops`, vazio (sem README, licença ou `.gitignore` do GitHub).
- [x] Em Settings > Actions > General, deixar "Workflow permissions" em "Read repository contents and packages permissions". Os workflows pedem só o que usam.
- [x] Em Settings > Pages, "Build and deployment", Source: GitHub Actions.
- [x] Em Settings > Security, ligar "Private vulnerability reporting" (o `SECURITY.md` aponta para ele) e os alertas do Dependabot.
- [x] Push da `main`:

  ```bash
  git remote add origin https://github.com/arthurjoanes/cycleops.git
  git push -u origin main
  ```

- [x] Ver o `ci` rodar em Actions: backend com integração, web, Compose sem internet, checagem de texto e gitleaks. Depois de conferir o resultado do commit, publique por Actions > pages > Run workflow, na `main`. O primeiro deploy cria o environment `github-pages`.
- [x] Abrir https://arthurjoanes.github.io/cycleops/ e conferir: o replay carrega (no DevTools, `events.ndjson.gz` chega como `application/gzip`, sem `Content-Encoding`), o mapa aparece com as estações e o console não mostra violação de CSP.
- [x] Pôr no `README.md` e no `README.en.md` o link da demo e o badge de CI, depois do primeiro CI verde:

  ```markdown
  [![ci](https://github.com/arthurjoanes/cycleops/actions/workflows/ci.yml/badge.svg)](https://github.com/arthurjoanes/cycleops/actions/workflows/ci.yml)

  Demo: https://arthurjoanes.github.io/cycleops/
  ```

- [x] Preencher o About: "Bike-share station dashboard for Oslo built on open GBFS data, with a priority queue and replay of recorded feeds." Tópicos: `react`, `typescript`, `fastapi`, `real-time`, `sse`, `gbfs`, `maplibre`, `data-visualization`, `postgresql`, `oslo`.
- [ ] Rodar uma vez o `feed-check` em Actions > feed-check > Run workflow e conferir que fez 5 requisições.
- [ ] Aceitar a atualização do Playwright para 1.64 quando o Dependabot abrir: ele ficou em 1.63.0 porque o pnpm recusa pacote com menos de um dia de publicado.

### Testes que pedem uma pessoa

- [ ] Preencher `docs/results/a11y-manual.md`, item a item.
- [ ] Opcional: deixar o live rodando na virada da 01:00 e das 05:00 em Oslo e anotar no `docs/results/live-manual.md` o fechamento, a abertura e uma resolução de incidente, que o teste curto não pegou.
