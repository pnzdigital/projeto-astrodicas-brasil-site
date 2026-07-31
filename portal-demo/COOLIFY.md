# Deploy do portal no Coolify

O portal é um site estático servido por Nginx. No Coolify, crie um recurso do
tipo **Dockerfile** apontando o repositório para `astrodicas-site/portal-demo`
(ou use essa pasta como build context).

Configuração:

- **Dockerfile:** `Dockerfile`
- **Porta publicada:** `80`
- **Health check:** `GET /healthz`
- **Build context:** `astrodicas-site/portal-demo`
- **Comando de start:** deixar o padrão da imagem

Depois de criar o serviço, conecte o domínio do portal e ative HTTPS no próprio
Coolify. Os links reais da Cakto entram em `portal-config.js`, nos mapas
`CHECKOUT.providers.cakto.checkoutUrls`, antes do deploy de produção.

O login, a liberação real de conteúdo e os webhooks de pagamento ainda devem
ser conectados a um backend do **canal web**. Não reutilize credenciais, IDs ou
webhooks do Telegram neste serviço.

## Argentina

O mesmo portal também responde em `/es` e carrega `portal-config-ar.js`:

- locale `es-AR` e moeda `ARS`;
- Mercado Pago transparente como adaptador padrão;
- URLs de checkout vazias até os links reais serem cadastrados;
- mesmos IDs `site:*`, sem misturar com o Telegram.

## Teste visual de cliente pago

Para abrir o protótipo com todos os produtos liberados, use:

`https://seu-dominio/?demo=paid`

Esse parâmetro só altera o estado visual no navegador. Ele não cria pagamento,
não grava dados no servidor e não substitui o webhook da Cakto.
