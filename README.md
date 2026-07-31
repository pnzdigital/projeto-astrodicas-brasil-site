# AstroDicas Web Site

Portal web premium do canal site AstroDicas. O Telegram é um sistema separado.

## Entradas

- Brasil: `/`
- Argentina: `/es/` com preços em ARS e Mercado Pago como adaptador padrão
- Ofertas Brasil: `/oferta-lua-1` (página V1, R$ 27,90/mês) e `/oferta-lua-2` (página V2, R$ 97,00 pagamento único)
- Ofertas Argentina: `/es/oferta-lua-1` (V1, ARS 8.649/mês) e `/es/oferta-lua-2` (V2, ARS 30.070 pagamento único), usando as mesmas páginas originais localizadas em espanhol argentino
- As páginas de venda originais ficam em `lp-plano-lua/v1` e `lp-plano-lua/v2`; as quatro rotas públicas apontam diretamente para elas.
- Área logada: `https://dash.astrodicas.pnzdigital.com.br/`

O checkout usa IDs `site:*` e URLs configuráveis por provedor. Nenhuma
credencial, URL ou webhook deve ser colocado neste repositório.

## Separação dos canais

O site e o Telegram pertencem à mesma empresa, mas são duas fontes de venda
independentes. Este projeto contém somente o canal web: usuários, sessões,
pedidos, webhooks, acessos e leituras usam o namespace `site:*`. Credenciais,
banco e regras do Telegram não devem ser importados para este banco.

## Runtime web

- `portal-demo/`: interface premium e variação argentina em `/es/`;
- `api/`: autenticação, perfil natal, pedidos, permissões e geração de leituras;
- PostgreSQL: persistência exclusiva do site;
- MiniMax: geração editorial configurada somente por variáveis do Coolify;
- Cakto e Mercado Pago: adaptadores de checkout/webhook, trocáveis por produto.

Variáveis de geração: `MINIMAX_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL_TEXT` e
`MINIMAX_TIMEOUT_SECONDS`. Se o provedor estiver temporariamente indisponível,
o portal entrega o fallback editorial local sem derrubar a área do cliente.

O horóscopo diário é renovado diariamente e exige três parágrafos substanciais.
Mapas permanentes são regenerados quando os dados do perfil mudam. O modelo não
deve inventar posições planetárias ainda não calculadas por um motor astronômico.
