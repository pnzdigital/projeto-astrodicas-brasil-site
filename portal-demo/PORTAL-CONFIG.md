# Contrato da configuração do portal

`portal-config.js` é a fonte de configuração do **portal web AstroDicas**.
Ele não configura o Telegram e não deve receber tokens, IDs de chat, webhooks ou
preços do bot. Os dois canais podem vender produtos com nomes parecidos, mas os
IDs daqui sempre começam por `site:`.

## O que existe no arquivo

- `SITE_CATALOG`: produtos individuais, Plano Lua, oferta Premium e os combos web.
- `WEB_CONTENT`: leituras entregues como experiência web. O PDF é uma saída opcional.
- `ACCESS_STATES`: estados de renderização/liberação (`available`, `locked`, `pending`, etc.).
- `CHECKOUT`: adaptadores de checkout. Cakto é o primeiro provedor, com URLs vazias até o deploy.
- `checkoutUrl(productId, provider)`: resolve o link sem acoplar o portal a uma API externa.

## Trocar Cakto por outro checkout

1. Preencha as URLs do produto em `CHECKOUT.providers.cakto.checkoutUrls`, ou habilite outro
   provedor (`kiwify`, `hotmart`, `stripe` ou `custom`).
2. Mude `CHECKOUT.defaultProvider` para o nome do adaptador escolhido.
3. Mantenha os mesmos IDs `site:*`; a liberação de conteúdo deve usar o evento interno do
   backend, não o formato específico do checkout.
4. Nunca copie URLs, credenciais ou webhooks do Telegram para este arquivo.

URLs em branco são seguras para desenvolvimento: um card pode aparecer no catálogo, mas o
botão de compra deve permanecer desabilitado até o provedor ter uma URL configurada.
