# Interface e navegação

O CycleOps concentra na tela principal o estado da fonte, as contagens da cidade e a disponibilidade de cada estação. Informações da sessão, análise, legendas e atribuições abrem quando solicitadas.

## Organização da tela

| Parte | Uso |
|---|---|
| Cabeçalho | Escolher a fonte e o fuso. O modo mostra a data da gravação ou a seed da simulação. “Dados da sessão” abre origem, publicação e cobertura. |
| Replay | Pausar, retomar, mudar velocidade e escolher um instante. “Ir para o incidente” abre a ocorrência escolhida. |
| Indicadores | Ler incidentes confirmados, operação desabilitada e cobertura. O ícone de gráfico abre “Analisar dados”. |
| Fila e catálogo | Comparar condição e contagens de bicicletas e vagas; selecionar uma estação para consultar motivo e histórico. |
| Mapa | Abrir uma estação pela posição. “Legenda” mostra os estados e símbolos usados. |
| Rodapé | Consultar “Fontes e licenças”, com atribuições de Oslo Bysykkel e OpenStreetMap. |

No celular, “Lista” e “Mapa” são botões de um grupo de visualização; o botão pressionado indica a visão atual. Tab percorre os dois e Enter ou espaço ativa a escolha. Os detalhes da estação abrem em uma gaveta. Ao fechar, o foco retorna à ação que abriu esses detalhes.

## Controles do replay

Pausa e retomada têm texto e ícone. Os ícones de “Passo” e “Copiar este momento” têm tooltip e nome acessível. O relógio mostra o instante virtual no fuso escolhido; os extremos da timeline mostram os limites da gravação.

“Incidentes”, identificado como “Explorar incidentes”, abre o seletor e a lista de confirmação e resolução. Uma estação selecionada filtra os marcos; o controle permite mostrar os demais. “Detalhes do replay”, no ícone de informação, mostra o contador de eventos e o término da reprodução. O salto rápido continua disponível com esses painéis recolhidos.

Arrastar a timeline pausa no instante escolhido. As setas movem um segundo, Page Up e Page Down movem um minuto, e Home e End vão aos limites. Os intervalos sem dados e de serviço fechado mantêm sua duração no eixo.

O [guia de exploração](explorar-dados.md) explica os links compartilhados, os rankings e os denominadores da análise.

## Catálogo e seleção

“Todas as estações” ordena por nome e começa com 50 linhas. “Mostrar mais estações” acrescenta 50; o contador anuncia o trecho exibido. A busca procura em todo o catálogo, incluindo estações ainda fora desse trecho. Mudar a busca reinicia a lista de resultados.

A seleção pelo mapa ou por uma URL pode ampliar a lista até incluir a estação escolhida. Ela mantém a ordem por nome. A fila de prioridades segue a ordem calculada pelo motor e mantém as linhas no lugar enquanto o ponteiro ou o foco estiverem dentro da lista.

As linhas mostram nome, condição e números de bicicletas e vagas. A justificativa completa aparece na seleção, junto dos detalhes. Valor desconhecido aparece como “?” e tem uma descrição acessível.

## Leitura e teclado

As ações compactas usam [SVGs locais](../apps/web/src/components/ui/ActionIcon.tsx) decorativos. Seus botões e resumos carregam o nome da ação. Cor, texto e símbolos identificam as condições das estações.

Tab percorre os controles; Enter ou espaço abre os resumos expansíveis. O foco tem contorno visível. Na gaveta e na análise, Escape fecha o painel e devolve o foco. O atalho “Ir para a fila” leva à lista, e “Ver as estações em lista” oferece a alternativa ao mapa. Sem WebGL, a busca e o catálogo ficam disponíveis.

As tabelas da análise permitem ler os valores dos gráficos. Fonte indisponível e fechamento aparecem como lacunas. Modo e data ou seed acompanham os painéis para identificar o período consultado.

## Implementação

| Responsabilidade | Código |
|---|---|
| Fonte, metadados e foco | [Header](../apps/web/src/app/Header.tsx) e [App](../apps/web/src/app/App.tsx) |
| Transporte e timeline | [ReplayControls](../apps/web/src/features/replay/ReplayControls.tsx) e [ReplayTimeline](../apps/web/src/features/replay/ReplayTimeline.tsx) |
| Fila e catálogo | [StationPriorityList](../apps/web/src/features/priorities/StationPriorityList.tsx) e [AllStationsList](../apps/web/src/features/stations/AllStationsList.tsx) |
| Indicadores e análise | [Indicators](../apps/web/src/features/priorities/Indicators.tsx) e [painéis](../apps/web/src/features/analysis/) |
| Legenda e atribuições | [Mapa](../apps/web/src/features/stations/StationAvailabilityMap.tsx) e [Footer](../apps/web/src/app/Footer.tsx) |

Os contratos continuam validados na entrada dos dados. O gerador cria um módulo por validador Ajv, com inicialização pura. O bundler mantém os validadores do servidor nos chunks do stream e do histórico; `validators.js` reúne os mesmos exports. Tipos e declarações mantêm a fronteira com o backend. Os comandos de geração e verificação estão no [CONTRIBUTING.md](../CONTRIBUTING.md#generated-contract-validation).

## Conferir a apresentação

Em `apps/web`, os cenários de UI verificam teclado, seleção, foco e disposição em desktop e celular:

```bash
corepack pnpm exec playwright test --project=e2e tests/e2e/uiux.spec.ts tests/e2e/replay-tools.spec.ts
corepack pnpm exec playwright test --project=media tests/media/uiux.spec.ts tests/media/analysis.spec.ts
```

O Playwright gera o build e abre um servidor próprio em 4188. `CYCLEOPS_WEB_TEST_PORT` seleciona outra porta disponível. As capturas ficam em `docs/media/`; testes e medições têm seus registros em [resultados](results/index.md). A revisão manual segue o [checklist de acessibilidade](results/a11y-manual.md).

Na revisão visual, conferir o modo e a data ou seed, textos das estações, contagens, foco e conteúdo dos painéis. Verificar também a lista ampliada, busca, legendas e créditos. As [regras de marca e legibilidade](brand/README.md) acompanham essa revisão.
