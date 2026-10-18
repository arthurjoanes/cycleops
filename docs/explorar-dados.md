# Explorar os dados

O replay permite voltar a um incidente e comparar o que a estação informou com a decisão do motor. A análise reúne o período da gravação, a evolução dos indicadores e as tentativas de coleta. No stream do Compose, os indicadores acompanham a sessão aberta e o relatório de qualidade consulta o banco.

## Fonte, horários e estações

O cabeçalho mantém o modo e a data da gravação, ou o modo “Simulação” com sua seed. O seletor “Horários” alterna entre Oslo e o fuso local. “Dados da sessão”, no ícone de informação ao lado do estado da fonte, abre a origem, a última publicação e a cobertura.

A fila mostra a condição e as contagens de bicicletas e vagas. Selecione a estação para ler o motivo completo e abrir seu histórico. No celular, esses detalhes aparecem em uma gaveta; Escape ou “Fechar” retorna à lista.

“Todas as estações” começa com 50 linhas e “Mostrar mais estações” acrescenta mais 50. A busca consulta o catálogo inteiro e reinicia o trecho exibido. Uma seleção pelo mapa ou por link pode ampliar esse trecho para incluir a estação escolhida. O contador informa quantas linhas estão visíveis e quantas correspondem à busca.

No mapa, “Legenda” abre todos os estados. O rodapé mantém os créditos resumidos; “Fontes e licenças” abre as atribuições e os links para os dados.

## Compartilhar um momento

Abra uma estação e escolha o horário no replay. O ícone de link, “Copiar este momento”, pausa a reprodução e cria um endereço com a fonte, a estação, as opções da tela e o instante escolhido. O link também inclui o hash dos eventos da gravação.

Quem abrir o endereço recebe a mesma posição, pausada. Se a gravação publicada tiver outro hash, a página explica a diferença e permite abrir do início. Um horário inválido também gera uma mensagem; ele não é convertido em outro instante. Quando o navegador não permite copiar automaticamente, o campo de texto mostra o endereço para cópia manual.

O parâmetro `artifact` identifica os eventos por SHA-256; `at` guarda os segundos desde `periodStart` do manifesto. Os marcos de incidente usam o `committedAt` da sequência correspondente, pois o primeiro evento pode ocorrer depois do início da gravação.

## Navegar pelo replay

A linha do tempo cobre o período da gravação. Arraste o controle ou use o teclado para escolher um horário; a reprodução pausa nesse ponto. Os marcadores distinguem confirmação e resolução. Ao selecionar uma estação, os marcadores se limitam aos incidentes dela.

“Ir para o incidente” abre a ocorrência escolhida. Em “Incidentes” (“Explorar incidentes”), altere a ocorrência no seletor, consulte os marcos ou desative o filtro da estação selecionada. O ícone de informação do replay, “Detalhes do replay”, mostra o evento atual, o total e o término da reprodução.

As posições seguem o tempo decorrido, inclusive durante falhas da fonte e fechamento do serviço. Pausa, velocidade e “Passo” permanecem na barra. As setas do teclado movem a timeline em um segundo; Page Up e Page Down, em um minuto. Home e End levam aos limites do período.

## Ler o resumo

Na faixa de indicadores, use o ícone de gráfico, “Analisar dados”, e abra “Resumo da gravação”. Em “Período observado”, escolha a gravação inteira, os primeiros 15 ou 30 minutos ou os 15 minutos finais. Os limites aparecem no fuso selecionado. O recorte atualiza os tempos e rankings sem mover o replay.

A barra de tempo da fonte separa dado válido, lacuna e serviço fechado, com durações textuais ao lado. Os contadores registram confirmações e resoluções dentro do recorte. A lista também inclui incidentes iniciados antes dele; resoluções posteriores aparecem como “Após o recorte”. A duração conhecida continua sendo a diferença entre os eventos do motor. Fechar a análise restaura a gravação inteira na próxima abertura.

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

O [guia da interface](ui-ux.md) descreve a organização da tela, os controles de teclado e os comandos para conferir a apresentação.

| Parte | Código |
|---|---|
| Link e posição do replay | [Estado da URL](../apps/web/src/app/useHashState.ts), [sessão](../apps/web/src/app/useDataSession.ts), [player](../apps/web/src/streams/replayPlayer.ts) |
| Navegação e marcadores | [Controles](../apps/web/src/features/replay/ReplayControls.tsx) e [timeline](../apps/web/src/features/replay/ReplayTimeline.tsx) |
| Resumo e indicadores | [Análise](../apps/web/src/features/analysis/) e [store](../apps/web/src/features/stations/store.ts) |
| Diagnóstico | [Contratos](../apps/backend/src/cycleops/contracts.py), [compilação do replay](../apps/backend/src/cycleops/application/replay.py), [consultas](../apps/backend/src/cycleops/adapters/postgres/queries.py) e [rotas](../apps/backend/src/cycleops/api/routes.py) |

O [Mobi Transit Explorer](https://github.com/adnanreza/mobi-transit-explorer) foi a referência para compartilhar a visão da análise; o [MapLibre Time Slider](https://github.com/opengeos/maplibre-gl-time-slider), para navegação temporal. Rankings de disponibilidade do [BiziDashboard](https://github.com/gcaguilar/bizidashboard), gráficos recebidos por SSE do [Real-Time Charts with FastAPI](https://github.com/roniemartinez/real-time-charts-with-fastapi) e a apresentação de resultados do [GBFS Validator](https://github.com/MobilityData/gbfs-validator) orientaram as demais funções. A implementação usa os contratos, eventos e componentes do CycleOps.
