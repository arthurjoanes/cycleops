# CycleOps

Lista as estações do Oslo Bysykkel sem bicicleta ou sem vaga, em ordem de prioridade, com o motivo e os dados usados no cálculo. Os dados vêm do feed GBFS aberto do operador.

![Tela do CycleOps no modo Simulação: fila de prioridades à esquerda, mapa de Oslo com as estações à direita e os detalhes de uma estação sem bicicletas abertos sobre o mapa](docs/media/desktop-1440.png)

Um vídeo de 27 s, [docs/media/demo.webm](docs/media/demo.webm), mostra a simulação pelo stream do servidor no Compose: um incidente confirmado no topo da fila e o motivo nos detalhes, a API reiniciando (conexão em "reconectando" e de volta a "conectada") e a troca para o replay em arquivo, pausado.

## Como rodar

Precisa só de Docker. Da raiz do repositório:

```bash
docker compose up --build
```

Abre em http://127.0.0.1:8080, na simulação: um worker gera estações com seed e passa pelo mesmo motor do feed real, a API entrega os eventos por SSE e o Caddy serve a interface. O banco é um PostgreSQL sem porta publicada. As senhas que o `compose.yaml` usa sem `.env` são inseguras de propósito e só servem para uso local; o `.env.example` mostra como trocar.

Para o feed real de Oslo, copie `.env.live.example` para `.env.live`, ponha o seu identificador no formato `<organização>-<aplicação>` e suba com outro nome de projeto, que separa o banco da simulação:

```bash
docker compose -p cycleops-live --env-file .env.live up --build
```

Depois de mudar o código, suba com `--build`: as imagens têm tag local fixa e um `up` simples reaproveita o build antigo. Só uma pilha publica a porta 8080 por vez; para trocar entre simulação, live e `scripts/checks`, pare a outra com `docker compose down` (ou `docker compose -p cycleops-live down`).

## Como rodar só a web

Precisa de Node 24 com corepack. Da raiz do repositório:

```bash
cd apps/web
corepack pnpm install
corepack pnpm dev
```

Abre em http://localhost:5173 e tenta primeiro o stream do servidor, pelo proxy para o edge do Compose em 127.0.0.1:8080. Sem a pilha no ar, a página mostra o erro de carga com o botão para abrir o replay em arquivo. Para ver o build do GitHub Pages, que só tem o replay, servido sob `/cycleops/` como no Pages:

```bash
corepack pnpm build:pages
corepack pnpm preview:pages
```

Abre em http://127.0.0.1:4173/cycleops/.

## Como testar

O que o CI roda, menos o gitleaks, localmente (Docker, git, uv, Node 24 com corepack e o Chromium do Playwright):

```bash
scripts/checks
```

Só a web, em `apps/web`:

```bash
corepack pnpm lint
corepack pnpm typecheck
corepack pnpm test
corepack pnpm exec playwright install chromium
corepack pnpm e2e
corepack pnpm check:bundle
```

O `e2e` gera o build do Pages e roda o Playwright contra ele. O `check:bundle` lê o build que já existe em `dist/`. Os comandos do backend Python, dos testes de integração com PostgreSQL e do E2E contra o Compose (`corepack pnpm e2e:compose`, que no fim apaga o banco do projeto `cycleops-e2e`) estão no `CONTRIBUTING.md`.

## Modos de dados

- Ao vivo: o worker lê o feed de Oslo a cada 15 s e a API manda os eventos por SSE. Roda só no Compose local.
- Simulação: estações e contagens geradas com seed, com a faixa "Simulação" sempre visível. No Compose vem do worker, pelo mesmo caminho do ao vivo; no Pages e no botão "Simulação (arquivo)", de um artefato gerado offline.
- Replay gravado: uma gravação real do feed passada pelo motor, reproduzida no navegador. Só existe quando uma gravação tem pelo menos um incidente confirmado e resolvido; ainda não há nenhuma.

O modo fica no cabeçalho o tempo todo, com a seed na simulação e o estado da conexão. Com a fonte fora, a tela mostra o erro e oferece o replay em arquivo; ela nunca troca sozinha. O replay tem pausa, 1×, 5×, 20×, passo e "ir para o incidente".

## Limitações

- Não é serviço oficial do Oslo Bysykkel.
- Só leitura, sem reserva nem despacho de equipe.
- O feed não tem SLA.
- O live roda só no Compose local.
- No Compose em HTTP/1.1, o navegador abre no máximo 6 conexões por origem, e cada aba segura uma para o SSE. A API também limita a 4 streams por IP: a quinta aba derruba a mais antiga.
- A base do mapa é simplificada e não serve para navegação.
- A CSP do Pages vai por meta e é mais fraca que a do Caddy (ver `SECURITY.md`).

## Fontes e licenças

- Dados do Oslo Bysykkel sob NLOD 2.0 e base do mapa do OpenStreetMap sob ODbL: [DATA_SOURCES.md](DATA_SOURCES.md).
- Software de terceiros: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
- Código sob a licença MIT: [LICENSE](LICENSE).

## Arquitetura

Processos, ligações e o teste que cobre cada uma em [docs/architecture.md](docs/architecture.md). Decisões em [docs/adr/](docs/adr/). Resultados de execução, com commit e comando, em [docs/results/](docs/results/).
