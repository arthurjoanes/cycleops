# Explorar os dados

O replay permite voltar a um incidente e comparar o que a estação informou com a decisão do motor. A análise reúne o período da gravação, a evolução dos indicadores e as tentativas de coleta. No stream do Compose, os indicadores acompanham a sessão aberta e o relatório de qualidade consulta o banco.

## Compartilhar um momento

Abra uma estação e escolha o horário no replay. O botão “Copiar este momento” pausa a reprodução e cria um endereço com a fonte, a estação, as opções da tela e o instante escolhido. O link também inclui o hash dos eventos da gravação.

Quem abrir o endereço recebe a mesma posição, pausada. Se a gravação publicada tiver outro hash, a página explica a diferença e permite abrir do início. Um horário inválido também gera uma mensagem; ele não é convertido em outro instante. Quando o navegador não permite copiar automaticamente, o campo de texto mostra o endereço para cópia manual.

O parâmetro `artifact` identifica os eventos por SHA-256; `at` guarda os segundos desde `periodStart` do manifesto. Os marcos de incidente usam o `committedAt` da sequência correspondente, pois o primeiro evento pode ocorrer depois do início da gravação.

## Navegar pelo replay

A linha do tempo cobre o período da gravação. Arraste o controle ou use o teclado para escolher um horário; a reprodução pausa nesse ponto. Os marcadores distinguem confirmação e resolução. Ao selecionar uma estação, os marcadores se limitam aos incidentes dela.

As posições seguem o tempo decorrido, inclusive durante falhas da fonte e fechamento do serviço. Os botões de pausa, velocidade, passo e salto para incidente continuam disponíveis.

## Ler o resumo

Em “Analisar dados”, abra “Resumo da gravação”. O relatório usa a gravação inteira, independentemente do ponto em que o player está pausado. Ele mostra a duração, incidentes confirmados e resolvidos e os períodos com fonte válida, sem dados ou com o serviço fechado.

O ranking soma o tempo observado com zero bicicletas ou zero vagas físicas por estação. Um intervalo só entra quando a fonte e o registro da estação permitem verificar aquela contagem. Vagas desconhecidas, estações fora do cadastro e períodos sem dados ficam fora do denominador; fechamento é apresentado à parte.

A cobertura global usa outra base: soma o tempo válido de cada estação e divide pelo tempo em que ela consta no catálogo com o serviço aberto. As lacunas entram nessa base; o período fechado fica separado. No ranking de vagas, estações virtuais e capacidade de devolução desconhecida ficam fora.

Esse tempo descreve disponibilidade observada. As contagens do feed não medem viagens realizadas nem passageiros atendidos. Uma estação recém-adicionada pode ter uma janela menor que as demais. Clique no nome para conferir seus detalhes.

## Acompanhar a cidade

“Evolução da cidade” mostra a quantidade de estações com incidentes confirmados de falta de bicicletas ou vagas, operação desabilitada e cobertura válida. As contagens usam as mesmas decisões exibidas nos indicadores da página.

A série guarda até 120 instantes de eventos aceitos nessa sessão. Mudança de fonte e snapshot de ressincronização reiniciam essa janela. Um salto no replay reconstrói os últimos 120 pontos até a posição escolhida. Fonte desconhecida, vencida, em quarentena ou fechada aparece como lacuna. A tabela alternativa permite consultar os mesmos valores do gráfico.

## Conferir a qualidade

“Qualidade do feed” separa respostas HTTP, falhas de transporte, erros de contrato e publicações rejeitadas. HTTP 200 ou 304 indica o resultado da requisição; uma resposta 200 ainda pode conter conteúdo inválido. Publicações repetidas, corrigidas, fora de ordem ou com horário futuro são contadas por resultado.

Cada feed tem suas contagens e as últimas cinco tentativas, com horário, status e motivo. Registros inválidos são somados por resposta validada, portanto uma estação pode aparecer em mais de uma publicação. Uma falha sem corpo utilizável não inventa uma contagem de registros.

No replay, `diagnostics.json` cobre o período inteiro. Seu SHA-256, fluxo, modo e janela precisam corresponder ao manifesto. A simulação permanece identificada no relatório. Artefatos antigos sem esse arquivo continuam reproduzíveis e informam que o relatório está indisponível.

No Compose, `GET /api/v1/feed-diagnostics` consulta as últimas seis horas por padrão. Os parâmetros `from` e `to` permitem uma janela de até 24 horas. A API continua somente leitura, com o mesmo limite de requisições. “Atualizar relatório” faz uma nova consulta.

## Implementação e referências

| Parte | Código |
|---|---|
| Link e posição do replay | [Estado da URL](../apps/web/src/app/useHashState.ts), [sessão](../apps/web/src/app/useDataSession.ts), [player](../apps/web/src/streams/replayPlayer.ts) |
| Navegação e marcadores | [Controles](../apps/web/src/features/replay/ReplayControls.tsx) e [timeline](../apps/web/src/features/replay/ReplayTimeline.tsx) |
| Resumo e indicadores | [Análise](../apps/web/src/features/analysis/) e [store](../apps/web/src/features/stations/store.ts) |
| Diagnóstico | [Contratos](../apps/backend/src/cycleops/contracts.py), [compilação do replay](../apps/backend/src/cycleops/application/replay.py), [consultas](../apps/backend/src/cycleops/adapters/postgres/queries.py) e [rotas](../apps/backend/src/cycleops/api/routes.py) |

O [Mobi Transit Explorer](https://github.com/adnanreza/mobi-transit-explorer) foi a referência para compartilhar a visão da análise; o [MapLibre Time Slider](https://github.com/opengeos/maplibre-gl-time-slider), para navegação temporal. Rankings de disponibilidade do [BiziDashboard](https://github.com/gcaguilar/bizidashboard), gráficos recebidos por SSE do [Real-Time Charts with FastAPI](https://github.com/roniemartinez/real-time-charts-with-fastapi) e a apresentação de resultados do [GBFS Validator](https://github.com/MobilityData/gbfs-validator) orientaram as demais funções. A implementação usa os contratos, eventos e componentes do CycleOps.
