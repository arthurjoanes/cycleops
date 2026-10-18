# Disponibilidade por período

A análise agora recorta os tempos observados de bicicletas e vagas. O seletor oferece a gravação inteira, os primeiros 15 ou 30 minutos e os 15 minutos finais. A posição do player continua separada desse recorte.

O cálculo usa os estados já emitidos pelo motor, inclusive o último estado anterior ao início da janela. Fonte sem dado verificável e serviço fechado mantêm seus tempos. Confirmações e resoluções são contadas pelo instante do evento; incidentes iniciados antes da janela permanecem consultáveis. Uma resolução posterior recebe “Após o recorte”, com duração indisponível nessa janela.

## Gravação consultada

Os eventos têm SHA-256 `48e2c7d55854510f1845b9ef2950aa0274a7c0f4363f090a71032f3b0eed0031`. O manifesto cobre 09/10/2026, de 22:25:27.795 a 23:25:15.533 UTC, já 10/10 no horário de Oslo. Não houve coleta nova.

| Recorte | Confirmações | Resoluções | Leitura |
| --- | --- | --- | --- |
| Gravação inteira | 40 | 2 | Resultado anterior preservado |
| Primeiros 15 min | 34 | 0 | Tempo observado por estação |
| Primeiros 30 min | 39 | 1 | Inclui a resolução de St. Olavs gate |
| 15 min finais | 0 | 0 | Serviço fechado; cobertura aberta indisponível |

A barra de tempo da fonte acompanha os valores textuais. Sua entrada dura 220 ms e é desativada por movimento reduzido. Os rankings continuam mostrando o tempo verificável de cada campo e seu denominador. Essas observações descrevem disponibilidade; o feed não mede viagens realizadas.

## Comandos executados

Código da consulta: `d78400d595d00d3425b7f32ccceb602713d9c6a1`. As verificações abaixo foram executadas localmente em `apps/web`, com os executáveis das dependências instaladas. O teste inicial falhou em duas asserções de recorte; a ampliação para uma janela inicial também falhou antes da implementação.

| Comando equivalente | Resultado |
| --- | --- |
| `corepack pnpm lint` | Sem erros |
| `corepack pnpm typecheck` | Sem erros |
| `corepack pnpm test` | 122 testes, 13 arquivos |
| `corepack pnpm build:pages` | Build concluído; conserva o aviso do chunk de mapa carregado sob demanda |
| `corepack pnpm check:bundle` | 125.695 bytes gzip; teto 127.026 |
| `CYCLEOPS_WEB_TEST_PORT=5183 corepack pnpm e2e` | 31 cenários; build já gerado servido em processo próprio nessa porta |
| `CYCLEOPS_WEB_TEST_PORT=5183 corepack pnpm exec playwright test --project=media tests/media/availability-period.spec.ts` | 2 cenários, 4 capturas reais |

O config temporário de execução substituiu apenas o comando de build do servidor por `node scripts/serve-pages.mjs`, pois o build já estava concluído. Os cenários e o Chromium 153 do Playwright 1.63.0 permaneceram os do projeto. O servidor foi criado e encerrado por cada execução, sem reutilizar serviço existente.

A conferência adicional em Edge usou 1440 × 1050 e 390 × 844. Os contadores foram comparados com os instantes dos eventos do artefato; o player permaneceu na mesma posição. Axe não apontou violações A/AA na análise nesses dois tamanhos. Movimento reduzido desativou a animação, e não houve overflow horizontal nem requisição à API. Teclado, foco e navegação também passaram nos cenários do projeto. Leitura com tecnologia assistiva e zoom nativo continuam fora dessa conferência automatizada.

Capturas: [mapa](../media/availability-desktop.png), [análise](../media/availability-analysis-desktop.png), [celular](../media/availability-mobile.png) e [análise no celular](../media/availability-analysis-mobile.png). O teste de mídia permite gerá-las novamente. Backend, PostgreSQL, coleta ao vivo e Compose não foram reexecutados nesta alteração de interface. Não houve publicação de Pages ou deploy.
