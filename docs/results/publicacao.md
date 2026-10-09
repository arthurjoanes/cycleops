# Publicação

Passos para pôr o repositório e a demo no GitHub. Os workflows já estão em `.github/workflows/`:

- `ci.yml`: backend (ruff, mypy, `migrate` a partir do zero, pytest com integração num PostgreSQL de service container, `generate-artifacts --check`), web (lint, typecheck, Vitest, build do Pages, `check:bundle`, Playwright sob `/cycleops/`), Compose sintético sem internet (smoke, saída do worker bloqueada e o E2E do Compose), checagem de texto e gitleaks. Roda em PR e em push na `main`.
- `pages.yml`: build e deploy do Pages. Roda depois de um `ci` verde em push na `main`, ou à mão.
- `feed-check.yml`: `inspect-feed` com 5 requisições, só à mão e só em `arthurjoanes/cycleops`.

Nenhum deles precisa de secret.

## Checklist

Os itens marcados já foram executados localmente. A publicação no GitHub e os testes que pedem uma pessoa continuam pendentes. A ordem importa só onde um passo depende do anterior.

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
