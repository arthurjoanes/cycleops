# Segurança

## Como reportar

Use o relato privado do GitHub, em Security > Report a vulnerability, no repositório. Não abra issue pública para falha de segurança.

## O que é guardado

A web só lê. Não tem login, cookie, analytics nem armazenamento no navegador; o estado da tela fica no hash da URL. A CSP limita as conexões à própria origem (`connect-src 'self'`).

No Compose, a API e o worker guardam só logs técnicos em JSON (rota, status, duração, ids de evento, contagens, erro por categoria), sem payload, no driver `local` do Docker com o padrão dele: até 5 arquivos de 20 MB por container, e o mais antigo sai. O banco guarda dados do feed e o que o motor calcula: eventos por 24 h, observações, tentativas de coleta e incidentes encerrados por 7 dias. Não há dado de visitante: a API não grava IP nem user agent, e os limites por IP ficam só na memória do processo.

O arquivo bruto do live é dividido por hora. Ao abrir um novo arquivo, a limpeza remove arquivos anteriores com última escrita há mais de 48 horas e depois os mais antigos enquanto o conjunto ultrapassa 500 MiB. O arquivo em uso é preservado; por isso o volume pode passar desse valor entre rotações.

A API é somente leitura e conecta com um papel que só tem SELECT. O PostgreSQL não publica porta, e a senha do superusuário fica só nos containers do banco e do `migrate`.

## CSP

```text
default-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; font-src 'self'; worker-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'
```

No GitHub Pages ela vai por `<meta>`, como primeiro elemento depois do charset, e por isso fica sem `frame-ancestors`, que não funciona em meta. O Pages também não deixa mandar headers próprios. A CSP do Pages é mais fraca que a do Caddy, que vai por header e tem `frame-ancestors 'none'`.

A única exceção é `style-src 'unsafe-inline'`. O Radix Dialog (gaveta de detalhes em telas estreitas) usa o react-remove-scroll, que injeta um `<style>` para travar a rolagem. No Pages a exceção fica, porque não há nonce por requisição. No Caddy, ela só sai com nonce por requisição no `index.html` e `setNonce` do `get-nonce` antes do primeiro render.

`script-src` não tem exceção nem `unsafe-eval`. Texto vindo do feed (nome de estação, popup do mapa) é sempre texto: o lint proíbe `dangerouslySetInnerHTML` e `innerHTML`, e o popup usa `setText`.

## Edge e API no Compose

- O Caddy manda, em toda resposta, a CSP acima com `frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin` e uma `Permissions-Policy` que desliga câmera, microfone, localização, sensores, pagamento e USB. O `index.html` vai com `Cache-Control: no-cache`.
- Só o edge publica porta, e só em `127.0.0.1:8080`. A API confia nos headers de proxy apenas do endereço fixo do edge, então um `X-Forwarded-For` forjado não muda o IP usado nos limites.
- Limites por instância da API: 60 requisições REST por minuto por IP (429 com `Retry-After`), 4 streams SSE por IP (o mais antigo recebe `stream_closed` com `superseded`) e 100 streams no total. Fila de no máximo 200 mensagens ou 4 MiB por cliente.
- Só rotas GET (HEAD e os outros métodos dão 405), sem CORS, sem `/docs` e sem `/openapi.json`. Erros respondem `{code, message, requestId, details}`, sem stack trace.
- Imagens próprias rodam com usuário sem privilégio, e as imagens de base são fixadas por tag e digest. O `.dockerignore` é uma lista de permissão.

