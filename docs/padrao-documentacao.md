# Padrão de documentação

O README do CycleOps segue a organização dos outros projetos do portfólio. Português e inglês mantêm os mesmos fatos, comandos e referências.

## Conteúdo e ordem

| Seção | Informação esperada |
|---|---|
| Visão geral | Problema, cenário de uso e o que foi implementado. Identificar dados reais, simulação e alcance local. |
| Demonstração | Captura com data e contexto, roteiro curto e um caso com entrada e resultado consultáveis. |
| Arquitetura | Componentes, fluxo dos dados, responsabilidades e links para a implementação. |
| Stack e decisões | Ícones das tecnologias, motivos das escolhas, custos e dependências fixadas. |
| Executar localmente | Pré-requisitos, comandos que existem no repositório e endereço da aplicação. |
| Testes e resultados | Como verificar; execuções identificadas por data e revisão, com seus resultados e limites. |
| Limites e segurança | Restrições de uso, controles implementados e pendências. |
| Documentação | Guia a consultar para cada pergunta ou tarefa. |
| Autor e licença | Contato, licença do código e atribuição dos dados e recursos visuais. |

## Escrita

Usar frases diretas e detalhes do CycleOps: o que recebe, qual regra aplica, onde grava e o que o navegador mostra. Uma escolha técnica deve explicar o motivo e seu custo. Evitar adjetivos de qualidade, slogans, repetição da introdução e relatos de como o documento foi escrito.

Uma captura mostra a interface naquele estado. Resultados de coleta, testes e desempenho apontam para a execução que os produziu. Atualizar o texto não muda a data nem o commit de uma medição. Preservar os registros históricos e manter propostas separadas do comportamento implementado.

## Apresentação e manutenção

- Manter as nove seções, os cinco atalhos de navegação e a marca com alternativa para fundo escuro.
- Usar os SVGs locais da stack em 64 × 64 px, com texto alternativo, e o ícone de contato em 24 × 24 px junto ao nome. Preservar origem e licença.
- Seguir as regras de marca e legibilidade descritas em [Identidade visual](brand/README.md).
- Manter capturas legíveis; imagens muito altas ficam como links. Diagramas de componentes ficam no README e sequências detalhadas em [Arquitetura](architecture.md).
- Conferir links relativos, âncoras e imagens ao editar. Preservar âncoras antigas quando mudar títulos.
- Não acrescentar testes, métricas, ferramentas ou serviços ao texto sem uma implementação ou um registro correspondente.
