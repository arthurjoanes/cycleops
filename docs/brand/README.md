# Identidade do CycleOps

![Marca CycleOps](cycleops-lockup.svg)

O símbolo combina C e O como duas rodas. O percurso entre elas termina em um ponto de estação. A abertura do C e o traço curvo dão direção ao desenho; o ponto liga a ideia de movimento ao estado de uma estação.

<a id="cores"></a>

## Legibilidade

Os estados mantêm rótulos e símbolos próprios. A cor não transmite uma condição sozinha.

Texto sobre branco tem contraste de 13,27:1; texto secundário, 5,57:1; botão com texto branco, 6,36:1. Contornos necessários de controles têm contraste de 3,33:1 sobre branco. O foco permanece visível em superfícies claras e no cabeçalho escuro.

## Tipografia e uso

Inter continua local à aplicação. A interface usa números tabulares e pesos regulares para as leituras; o nome usa peso 720 e espaçamento mais justo. A marca ocupa uma faixa curta, preservando espaço para fila e mapa.

- [Símbolo SVG](../../apps/web/public/brand/cycleops-mark.svg): interface e usos sem o nome.
- [Marca com nome](cycleops-lockup.svg): README e documentação, com alternativa para fundo escuro em `cycleops-lockup-light.svg`.
- [Favicon](../../apps/web/public/favicon.svg): C e ponto de estação simplificados para tamanhos pequenos.

Manter a proporção e uma margem livre equivalente ao diâmetro do ponto superior. Na interface, o símbolo mede 48 × 32 px. Sobre petróleo, usar a versão branca. Não aplicar sombra, gradiente ou animação contínua à marca.

## Ícones e leitura da interface

Os [ícones de ação](../../apps/web/src/components/ui/ActionIcon.tsx) são SVGs locais de 18 px, desenhados com traço e a cor do controle. Eles acompanham transporte, compartilhamento, análise e informações da sessão. Cada botão mantém um nome acessível; o SVG é decorativo e fica fora da navegação por teclado.

Pausa e retomada mantêm texto junto do símbolo. Passo, compartilhamento e análise usam ícones com o nome da ação no tooltip. Nas contagens, bicicleta e vaga têm números alinhados; o texto acessível identifica o campo e informa quando o valor é desconhecido.

O modo e o estado da fonte permanecem no cabeçalho. Metadados, legendas e licenças abrem em controles próprios. A fila reúne condição e contagens; selecionar uma estação mostra a justificativa e o histórico. A faixa de indicadores concentra as contagens da cidade e o acesso à análise.

Espaçamento, alinhamento e contraste sustentam a leitura; o [guia da interface](../ui-ux.md) registra os controles e sua verificação.

## Apresentação no GitHub

A navegação do README reúne demo, arquitetura, execução, resultados e LinkedIn. Os rótulos identificam o destino e os SVGs ficam em `docs/readme/badges/`.

A stack usa ícones Devicon locais de 64 × 64 px com texto alternativo; o contato usa LinkedIn de 24 × 24 px acompanhado do nome. As cores originais dos ícones são preservadas. A [licença MIT](../stack/LICENSE.devicon) acompanha os arquivos e cada SVG registra a origem.
