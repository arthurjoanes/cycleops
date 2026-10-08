# ADR 0001: escopo do MVP e o que fica para depois

Status: Aceito por Arthur Joanes em 2026-10-06.

## Contexto

O CycleOps lista as estações do Oslo Bysykkel sem bicicleta ou sem vaga, em ordem de prioridade, com o motivo e os dados usados no cálculo. A fonte é o feed GBFS aberto do Oslo Bysykkel, sob a NLOD 2.0.

O foco do projeto é a tela: dado ao vivo, dado vencido, replay de gravação e mapa. O backend existe para alimentar essa tela, e cada peça de infraestrutura precisa de um motivo vindo dela.

## Decisão

- O MVP é somente leitura e sem login. Não reserva bicicleta, não despacha equipe e não cria tarefa.
- Sem Redis e sem broker. O PostgreSQL guarda o estado e é o único caminho de eventos até o navegador.
- O replay roda no navegador, sobre eventos gerados offline pelo mesmo motor Python que roda no worker.
- Observabilidade no MVP: logs JSON e um endpoint de saúde do feed. Sem métricas e sem OpenTelemetry.
- Resultados de execução (coleta, desempenho, testes manuais) ficam em `docs/results/`.
- Uma peça de backend ou de infraestrutura só entra se a interface depende dela. Ficam de fora, até um ADR aceito com necessidade medida: Redis ou outro broker, Kafka, Kubernetes, IA, microfrontends, lakehouse, gRPC, otimizador de rotas, Web Worker próprio para filtro ou ordenação, virtualização de lista, biblioteca de gráficos, provedor de mapa pago e serviço SaaS obrigatório.

Depois do MVP, o primeiro item é o live público em VPS, somente leitura. Login OIDC com tarefas e a parte de observabilidade e carga vêm depois. Cada item depende de um ADR aceito, com o gatilho e o custo.

## Alternativas

- Login e tarefas já no MVP: aumentam muito o escopo (sessão, CSRF, papéis, conflito de edição) e tiram tempo da tela.
- Redis Streams ou broker para os eventos: com uma publicação a cada 15 s, um `event_log` no PostgreSQL com LISTEN/NOTIFY atende e tira um serviço do Compose.
- Replay no servidor: exigiria estado por visitante e um backend no ar para a demo. No navegador, a demo roda estática no GitHub Pages.

## Consequências

- A demo pública é estática (replay e simulação) e não depende de servidor.
- O modo live roda só no Compose local até o item de VPS ser aceito.
- Sem métricas, a investigação de problema depende dos logs e do endpoint de saúde do feed.

## Risco

- O feed não tem SLA. Mudança de formato ou queda longa deixa o live parado; o replay gravado continua funcionando.
- O MVP pode parecer pequeno para quem procura login e escrita. O texto do README deixa claro que é só leitura.

## Quando revisar

- Quando um dos itens de depois do MVP for pedido.
- Quando uma medição mostrar que o PostgreSQL não dá conta da entrega de eventos (p95 de commit até o socket acima de 1 s).
