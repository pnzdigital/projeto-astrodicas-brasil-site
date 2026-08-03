# AstroDicas Web Site

Portal web premium do canal site AstroDicas. O Telegram é um sistema separado.

## Entradas

- Brasil: `/`
- Argentina: `/es/` com preços em ARS e Mercado Pago como adaptador padrão
- Ofertas Brasil: `/oferta-lua-1` (página V1, R$ 27,90/mês) e `/oferta-lua-2` (página V2, R$ 97,00 pagamento único)
- Ofertas Argentina: `/es/oferta-lua-1` (V1, ARS 8.649/mês) e `/es/oferta-lua-2` (V2, ARS 30.070 pagamento único), usando as mesmas páginas originais localizadas em espanhol argentino
- As páginas de venda originais ficam em `lp-plano-lua/v1` e `lp-plano-lua/v2`; as quatro rotas públicas apontam diretamente para elas.
- Área logada exclusiva do portal: `https://dash.astrodicas.pnzdigital.com.br/`
- As rotas `/oferta-lua-*` e `/es/oferta-lua-*` existem somente no host público `astrodicas.pnzdigital.com.br`; o host `dash` não serve páginas de venda.

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

## Rodando a API localmente

```bash
cd api
python -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
cp ../.env.example ../.env  # ajuste os valores locais; nunca versione o .env
```

Variáveis mínimas para dev (`ENV=development`, `ALLOW_INSECURE_DEV=1` liberam os
webhooks sem segredo configurado; nenhuma das duas tem efeito se `ENV=production`):

```bash
export DATABASE_URL=sqlite:///data/dev.db
export SITE_SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
export ENV=development
export ALLOW_INSECURE_DEV=1
export ALLOW_DEMO=1
export SITE_ORIGIN=http://localhost:8080
export COOKIE_SECURE=0
```

Subir o servidor de desenvolvimento:

```bash
.venv/bin/uvicorn app.main:app --reload --port 8080
```

`GET /api/health` confirma que subiu. Em produção (`ENV=production`) o app
recusa iniciar (`RuntimeError` no startup) se faltar `SITE_SECRET_KEY`,
`MP_WEBHOOK_SECRET`, `CAKTO_WEBHOOK_SECRET`, `ADMIN_PASSWORD` ou se
`COOKIE_SECURE` não for `1` — não existe fallback inseguro silencioso, e
`ALLOW_INSECURE_DEV`/`ALLOW_DEMO` são sempre ignorados nesse ambiente.

## Rodando a suíte de testes

```bash
cd api
python -m venv .venv   # se ainda não existir
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Com cobertura:

```bash
.venv/bin/pip install pytest-cov
.venv/bin/python -m pytest -q --cov=app --cov-report=term-missing
```

Os testes usam SQLite em arquivo temporário e não tocam nenhuma credencial
real (`conftest.py` já define `SITE_SECRET_KEY`, `ENV=test`,
`ALLOW_INSECURE_DEV=1` e `RATE_LIMIT_ENABLED=0` só para o processo de teste).

## Rate limiting

Login/registro (site e admin), abertura de pedido de checkout e os webhooks
(Cakto e Mercado Pago) passam por um limitador in-memory por IP, janela
deslizante, sem dependência externa (`api/app/ratelimit.py`). Acima do limite
a resposta é `429` com header `Retry-After` em segundos.

| Variável | Default | Rota(s) |
|---|---|---|
| `RATE_LIMIT_ENABLED` | `1` | liga/desliga tudo |
| `RATE_LIMIT_AUTH_MAX` / `RATE_LIMIT_AUTH_WINDOW_SECONDS` | `10` / `60` | `/api/auth/register`, `/api/auth/login`, `/api/admin/login` |
| `RATE_LIMIT_CHECKOUT_MAX` / `RATE_LIMIT_CHECKOUT_WINDOW_SECONDS` | `20` / `60` | `/api/checkout/order` |
| `RATE_LIMIT_WEBHOOK_MAX` / `RATE_LIMIT_WEBHOOK_WINDOW_SECONDS` | `120` / `60` | `/api/webhooks/{provider}`, `/api/webhooks/mercadopago/notify` |
| `RATE_LIMIT_BYPASS_IPS` | vazio | lista de IPs (separados por vírgula) sempre liberados |

O limite de webhook é alto de propósito: a assinatura HMAC já impede forjar
evento, então esta camada existe só contra flood/DoS — o Mercado Pago reenvia
notificações em burst quando o merchant fica offline por um tempo, e um
limite apertado demais derrubaria eventos legítimos e deixaria vendas sem
liberar acesso. Ajuste as variáveis com essa consequência em mente, e use
`RATE_LIMIT_BYPASS_IPS` para fixar IPs oficiais do provedor sem limite algum.

Escala de um único processo/worker (estado em memória, não compartilhado
entre réplicas). Para múltiplos workers atrás de um load balancer, trocar por
um backend compartilhado (ex.: Redis) se o volume justificar.
