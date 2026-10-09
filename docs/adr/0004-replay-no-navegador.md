# ADR 0004: replay no navegador com Clock e compile-replay

Status: Proposto, 2026-10-08, revisto em 2026-10-09 com a gravação real aceita.

## Contexto

A demo pública roda no GitHub Pages, sem servidor. Ela precisa mostrar o mesmo comportamento do live: incidentes confirmados e resolvidos, fonte vencida, fechamento noturno. As regras de prioridade, frescor e horário de serviço estão no motor Python (`cycleops.domain`), e o worker usa o mesmo motor.

## Decisão

- O `compile-replay` roda o adaptador GBFS e o `step` do motor com um Clock virtual sobre uma gravação do feed ou sobre o gerador sintético com seed. Os ticks caem a cada 5 s, contados do epoch; no empate, a entrada gravada vem antes do tick.
- Tentativas que falharam viram `PublicationFailed`, com o breaker e a quarentena do módulo de coleta rodados sobre as tentativas gravadas.
- A saída fica em `apps/web/public/replay/recorded/` e `apps/web/public/replay/synthetic/`: `events.ndjson.gz` com os eventos canônicos, `catalog.json` com cada versão do cadastro usada e `manifest.json` com hashes, versões, período, seed e os offsets dos incidentes confirmados e resolvidos.
- Rodar duas vezes dá o mesmo conteúdo: gzip sem nome e com `mtime=0`, sem instante de geração no manifesto, ids de incidente e de fluxo derivados do conteúdo. A comparação é sobre o conteúdo descompactado, porque o deflate muda de uma zlib para outra.
- Os artefatos são versionados e são zona gerada. `generate-artifacts` regenera; `generate-artifacts --check` acusa diferença.
- O navegador só reproduz: pausa, 1x, 5x, 20x, passo e "ir para o incidente" pelos offsets do manifesto. Nenhuma regra é recalculada no cliente.
- Uma gravação só vira replay se passar no aceite: pelo menos um incidente que vai de `confirmed` a `resolved` dentro dela. Sem gravação aceita, a demo abre na simulação, rotulada como tal.

A gravação iniciada em 2026-10-09 (UTC) passou no aceite: o incidente de falta de vagas em St. Olavs gate (estação 537) foi confirmado e resolvido dentro do período gravado. O build do Pages abre no replay real e mantém a simulação disponível no seletor. O período, os hashes e o resultado do aceite estão em [feed-validation.md](../results/feed-validation.md); os comandos e o commit, em [index.md](../results/index.md).

As amostras anteriores sem resolução continuam como registros da coleta, sem serem usadas como replay da demo. As amostras diurnas adicionais para avaliar movimento, valet e tempos de download continuam pendentes.

O que mudou com o Compose:

- O mesmo player roda no Pages e no Caddy. No Compose ele fica ao lado do stream do servidor, e a troca é sempre por botão; com a fonte fora, a tela oferece o replay sem trocar sozinha.
- Antes de tocar, o navegador confere os três arquivos entre si: `streamId` igual ao do manifesto, sequências de 1 a `eventCount` sem buraco e `catalogVersion` presente no catálogo. Uma mistura de dois deploys vira a mensagem de versão nova em vez de uma tela parada.
- O `feedHealth` dos eventos ganhou `stateSince`, o instante em que a fonte entrou no estado. O artefato foi regerado e o player não mudou.
- Com o live no ar, a gravação para o replay sai do arquivo bruto do worker (`record-feed --from-archive`) e passa pelo mesmo aceite.

## Alternativas

- Replay no servidor, com o worker lendo a gravação: exige backend no ar para a demo e estado por visitante.
- Reescrever as regras em TypeScript para o navegador recalcular: duas implementações do mesmo motor, que divergem com o tempo.
- Vídeo ou GIF da tela: não deixa pausar, avançar evento a evento nem abrir os detalhes de uma estação.

## Consequências

- Mudar a política, o adaptador ou o motor muda os artefatos; o `--check` mostra quando é preciso regenerar.
- O build do Pages e a imagem do Caddy só copiam arquivos e não precisam de Python.
- O salto para o incidente reaplica os eventos desde o início, sem checkpoint.

## Risco

- Uma gravação longa pode passar do teto de 2 MB do `events.ndjson.gz`. Hoje o `compile-replay` para com erro nesse caso; cortar a janela em volta dos offsets do manifesto fica para quando uma gravação real precisar.
- Reprocessar uma gravação não reproduz exatamente o live, porque a ordem entre entrada e tick pode diferir em milissegundos.

## Quando revisar

- Gravação com mais de 5.000 eventos ou salto acima de 200 ms com a CPU 4x mais lenta: considerar checkpoints.
- Artefato acima de 2 MB.
- Mudança incompatível no schema dos eventos.
