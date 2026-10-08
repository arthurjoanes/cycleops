# Segurança

## Como reportar

Use o relato privado do GitHub, em Security > Report a vulnerability, no repositório. Não abra issue pública para falha de segurança.

## O que é guardado

A web é estática e só lê. Não tem login, cookie, analytics nem armazenamento no navegador; o estado da tela fica no hash da URL. A CSP limita as conexões à própria origem (`connect-src 'self'`).

Quando a API e o worker existirem, eles guardam só logs técnicos em JSON, sem payload inteiro e com retenção definida.

## CSP

```text
default-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; font-src 'self'; worker-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'
```

No GitHub Pages ela vai por `<meta>`, como primeiro elemento depois do charset, e por isso fica sem `frame-ancestors`, que não funciona em meta. O Pages também não deixa mandar headers próprios. A CSP do Pages é mais fraca que a do Caddy, que vai por header e tem `frame-ancestors 'none'`.

A única exceção é `style-src 'unsafe-inline'`. O Radix Dialog (gaveta de detalhes em telas estreitas) usa o react-remove-scroll, que injeta um `<style>` para travar a rolagem. No Pages a exceção fica, porque não há nonce por requisição. No Caddy, ela só sai com nonce por requisição no `index.html` e `setNonce` do `get-nonce` antes do primeiro render.

`script-src` não tem exceção nem `unsafe-eval`. Texto vindo do feed (nome de estação, popup do mapa) é sempre texto: o lint proíbe `dangerouslySetInnerHTML` e `innerHTML`, e o popup usa `setText`.
