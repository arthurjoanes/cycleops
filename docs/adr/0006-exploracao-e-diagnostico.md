# ADR 0006: exploração do replay e diagnóstico de coleta

Status: Aceito, 2026-10-11.

## Contexto

A fila mostra o estado de cada estação no instante exibido. Para examinar uma gravação, faltavam um link para esse instante, navegação temporal e números sobre o período inteiro. O resultado HTTP também precisava ser separado da publicação que o motor aceita.

## Decisão

- O link guarda o hash dos eventos e os segundos desde o início do manifesto. Abre pausado; outro hash ou horário inválido gera uma mensagem. A timeline localiza confirmação e resolução pela sequência do evento.
- O resumo integra os estados produzidos pelo motor entre eventos. O ranking usa tempo observado com contagem física em zero; a cobertura usa estação-tempo durante o serviço aberto. Campos desconhecidos e estações virtuais têm tratamento próprio.
- A evolução da cidade mantém até 120 pontos aceitos no store. Atualiza apenas as estações alteradas, reinicia após ressincronização e reconstrói o prefixo ao reposicionar o replay. Fonte indisponível ou fechada aparece como lacuna.
- O compilador gera `diagnostics.json` a partir das tentativas gravadas e dos resultados do motor. O manifesto liga esse relatório por SHA-256, fluxo, modo e período.
- No Compose, `/api/v1/feed-diagnostics` lê as tentativas existentes no PostgreSQL. Usa a role somente leitura, janela padrão de seis horas e limite de 24 horas. A consulta não chama o operador.
- O painel de análise carrega sob demanda. O build permanece dentro do teto existente; nenhum pacote de gráficos é acrescentado.

## Consequências

O resumo cobre a gravação inteira, enquanto o gráfico acompanha a sessão ou o prefixo reproduzido. Seus denominadores ficam visíveis. As contagens do feed descrevem disponibilidade; não permitem calcular viagens.

HTTP 200, registros válidos, publicações aceitas e eventos emitidos são unidades diferentes. O diagnóstico mostra cada uma separadamente. Artefatos antigos sem diagnóstico continuam reproduzíveis.

As regras de confirmação, histerese, frescor e coleta continuam no motor e na política. A exploração usa os resultados desses componentes.

## Quando revisar

Reavaliar o cálculo local se uma gravação crescer além dos limites do replay ou se o resumo bloquear a interação. Para comparação entre dias, será necessária uma consulta histórica com janela e denominadores definidos.

Uso e referências: [explorar-dados.md](../explorar-dados.md). Validação: [exploracao-20261011.md](../results/exploracao-20261011.md).
