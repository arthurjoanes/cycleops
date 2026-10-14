# Acessibilidade: checklist manual

O alvo é WCAG 2.2 AA. O axe roda no E2E (`apps/web/tests/e2e/pages.spec.ts`) e não acha violação séria ou crítica, mas não cobre o que está aqui. Esta lista é preenchida por uma pessoa, item a item. Ainda não foi preenchida.

## Como abrir

- Replay (Pages): em `apps/web`, `corepack pnpm build:pages` e `corepack pnpm preview:pages`, depois http://127.0.0.1:4173/cycleops/.
- Stream do servidor (Compose): `docker compose up --build` na raiz, depois http://127.0.0.1:8080.

Para cada item, anote o resultado (passou, falhou ou parcial, com o que viu), o navegador com a versão, o leitor de tela com a versão (ou "nenhum"), a data e quem testou. Um item que falha vira issue com o passo a passo.

## Itens

### 1. Replay só por teclado

Sem mouse, no replay: pausar, mudar a velocidade para 5× e 20×, avançar um evento com "Passo", escolher um incidente e usar "Ir para o incidente", ver o incidente confirmado e depois resolvido. Abrir os detalhes pela fila e fechar com "Fechar"; em tela estreita, abrir a gaveta e fechar com Esc. O foco fica sempre visível, vai para o título dos detalhes ao abrir e volta para quem abriu ao fechar. O link "Ir para a fila" (primeiro Tab) leva ao título da fila sem mudar a URL.

| Resultado | Navegador | Leitor de tela | Data | Quem testou |
|---|---|---|---|---|
| | | | | |

### 2. NVDA na fila, nos detalhes e em "Todas as estações"

Com o NVDA: a fila é anunciada como lista, cada linha lê nome, condição, desde quando e motivo; o aviso "Prioridades mudaram. Reordenar" é alcançável e não move a lista sob o foco. Nos detalhes, as seções e a tabela da linha do tempo são navegáveis. Em "Todas as estações", a busca anuncia quantas estações sobraram e "Bjorvika" acha "Bjørvika". O mapa tem rótulo e o link para a lista.

| Resultado | Navegador | Leitor de tela | Data | Quem testou |
|---|---|---|---|---|
| | | | | |

### 3. Zoom de 200% e reflow a 320 px

Zoom do navegador em 200% numa janela de 1280 px, e a janela em 320 px de largura: nada some, nada exige rolagem horizontal, os controles do replay quebram linha sem cortar texto, a gaveta de detalhes rola.

| Resultado | Navegador | Leitor de tela | Data | Quem testou |
|---|---|---|---|---|
| | | | | |

### 4. Contraste dos tokens de estado

Com uma ferramenta de contraste: 4,5:1 nos textos das etiquetas de condição (sem bicicletas, sem vagas, em confirmação, recuperando, dados vencidos), nos textos secundários e na fila esmaecida (fonte vencida); 3:1 nos símbolos do mapa, nas linhas e faixas do gráfico de degraus e no contorno de foco. Nenhuma informação só pela cor: cada condição tem símbolo ou texto.

| Resultado | Navegador | Leitor de tela | Data | Quem testou |
|---|---|---|---|---|
| | | | | |

### 5. `prefers-reduced-motion`

Com a redução de movimento ligada no sistema: nenhuma animação de entrada ou de transição na fila, na gaveta e no mapa além do que o próprio movimento do usuário causa.

| Resultado | Navegador | Leitor de tela | Data | Quem testou |
|---|---|---|---|---|
| | | | | |

### 6. Ritmo do `aria-live`

Com o NVDA, no replay a 1×: no máximo um resumo a cada 10 s, sem narrar cada evento. A 5× e a 20×: nenhum anúncio de mudança; ao pausar e no fim do replay, um resumo do ponto atual. No Compose, a mesma regra no stream do servidor, e as mensagens de conexão (reconectando, suspeita, encerrada) anunciadas uma vez.

| Resultado | Navegador | Leitor de tela | Data | Quem testou |
|---|---|---|---|---|
| | | | | |

### 7. Alvos de toque

Num telefone ou com a emulação de toque do navegador: botões Lista e Mapa, demais botões, linhas da fila e da lista, "Fechar" e o seletor de incidente têm pelo menos 24×24 px e não se sobrepõem.

| Resultado | Navegador | Leitor de tela | Data | Quem testou |
|---|---|---|---|---|
| | | | | |
