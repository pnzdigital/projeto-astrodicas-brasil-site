# DEPLOY.md — AstroDicas Site

Runbook para deploy em Coolify (ou compose.yaml local).

## Pré-requisitos

- **Domínios:** `astrodicas.pnzdigital.com.br` (público + vendas) + `dash.astrodicas.pnzdigital.com.br` (portal logado)
- **DNS:** Ambos apontando pro Coolify/reverse proxy
- **TLS:** Proxy reverso termina HTTPS; containers falam HTTP (nginx.runtime.conf:1-4)
- **Banco:** PostgreSQL 16+ com acesso de rede ou volume local
- **Contas externas:** Mercado Pago (Argentina), Cakto (Brasil), Resend (e-mail), MiniMax (LLM)

---

## Variáveis de Ambiente

Obrigatórias marcar com ⚠️.

| Variável | Obrigatória | Onde obter | Quebra se faltar |
|----------|---|---|---|
| `SITE_SECRET_KEY` | ⚠️ | Gera: `openssl rand -hex 32` | 500 na autenticação/sessão |
| `DATABASE_URL` | ⚠️ | `postgresql+psycopg://user:pass@host:5432/db` | API não inicia |
| `SITE_ORIGIN` | Não | `https://astrodicas.pnzdigital.com.br,https://dash.astrodicas.pnzdigital.com.br` | CORS fails; dev default OK |
| `SITE_PUBLIC_URL` | Não | `https://astrodicas.pnzdigital.com.br` | Redirecionamentos quebrados |
| `PORTAL_URL` | Não | `https://dash.astrodicas.pnzdigital.com.br/` | Links pós-checkout errados |
| `COOKIE_SECURE` | Não | `1` (prod) ou `0` (dev) | Cookies acessíveis em HTTP |
| `ENV` | Não | `production` (prod) ou `development` (dev) | Flags de segurança dormem; demo ativa sem avisar |
| `ALLOW_DEMO` | Não | `0` (prod) ou `1` (staging) | Demo vitrine ativa em non-prod |
| `ALLOW_INSECURE_DEV` | Não | `0` (prod) ou `1` (dev) | Webhook validation saltada; rejeita MP/Cakto em prod |
| `MP_ACCESS_TOKEN` | ⚠️ (se MP ligado) | Painel MP > Suas integrações > Credenciais | 404 em `/checkout/mercadopago` |
| `MP_PUBLIC_KEY` | Não (se MP ligado) | Painel MP > Credenciais (pública, OK expor) | Formulário MP quebrado no frontend |
| `MP_WEBHOOK_SECRET_AR` | ⚠️ (se MP ligado) | Painel MP (app argentina) > Webhooks > Clave secreta | 401 na rota AR; venda paga não é liberada |
| `MP_WEBHOOK_SECRET` | Não (legado) | Clave secreta da aplicação antiga | Só afeta pagamentos abertos antes do deploy |
| `MP_STATEMENT_DESCRIPTOR` | Não | `ASTRODICAS` (padrão) ou custom | Nome da loja na fatura do cliente |
| `CAKTO_WEBHOOK_SECRET` | ⚠️ (se Cakto ligado) | Painel Cakto > Webhooks > Chave secreta | 403 webhook rejeitado; pedido não confirma |
| `RESEND_API_KEY` | Não | Painel Resend > API keys | Confirmação e reset de senha por e-mail falham silenciosamente |
| `MAIL_FROM` | Não | `AstroDicas <naoresponda@pnzdigital.com.br>` | Remetente padrão nos e-mails |
| `MINIMAX_API_KEY` | Não | Painel MiniMax > Credenciais | Leituras usam fallback editorial local (OK) |
| `LLM_BASE_URL` | Não | `https://api.minimax.io/v1` (padrão) | Fallback para MINIMAX_BASE_URL |
| `LLM_MODEL_TEXT` | Não | `MiniMax-M2.1` (padrão) | Fallback para MINIMAX_MODEL |
| `MINIMAX_TIMEOUT_SECONDS` | Não | `120` (padrão) | Timeout MiniMax em segundos |
| `MINIMAX_MAX_ATTEMPTS` | Não | `3` (padrão) | Tentativas quando o modelo derrapa de idioma; `1` entrega leitura com caractere estrangeiro |
| `RESEND_TIMEOUT_SECONDS` | Não | `15` (padrão) | Timeout Resend em segundos |
| `GEOCODING_TIMEOUT_SECONDS` | Não | `8` (padrão) | Timeout geocodificação em segundos |
| `MP_TIMEOUT_SECONDS` | Não | `20` (padrão) | Timeout Mercado Pago em segundos |
| `GEOCODING_ENABLED` | Não | `1` (ativar) ou `0` (desativar) | Coordenadas de nascimento não resolvem |
| `ADMIN_PASSWORD` | ⚠️ (se usar /admin) | Gera: senha segura, 16+ chars | `/admin` indisponível; login recusa |
| `ROLE` | Não (interno) | `public` (vitrine) ou `dash` (portal) | Scripts dev; Coolify ignora |
| `PORT` | Não (interno) | `8080` (padrão) | Scripts dev; Coolify ignora |

**Em Produção:** ⚠️ todas as variáveis são obrigatórias se o serviço as usa.  
**NUNCA:** valores reais em `.env.example`, CLAUDE.md, ou histórico Git.

---

## Ordem de Deploy

### 1. Postgres
- Volume para `/var/lib/postgresql/data` persistente
- Healthcheck: `pg_isready -U astrodicas -d astrodicas`
- Credenciais: `POSTGRES_USER=astrodicas`, senha secret-managed

### 2. API (`api/Dockerfile`)
- Dependência: postgres healthy
- Porta: 8000 (interno); proxy reverso expõe HTTPS
- Startup: auto-cria schema + migrations (SQLAlchemy Base.metadata.create_all)
- Healthcheck: `curl http://localhost:8000/api/health` → `{"ok": true, ...}`

### 3. Frontend (`portal-demo/Dockerfile`)
- Dependência: api started (não health; apenas started)
- Porta: 80 (interno); proxy reverso mapeia para HTTPS
- Hosts: Rotar via nginx.runtime.conf (`Host: astrodicas...` → storefront; `Host: dash...` → index/admin)
- Roteamento: `/api/` → proxy para api:8000

---

## Webhooks

### Mercado Pago

1. **Configurar:**
   - Painel > Webhooks > Novo
   - URL: `https://astrodicas.pnzdigital.com.br/api/webhooks/mercadopago/ar/notify`
   - Uma rota por aplicação do MP. A clave secreta desta aplicação vai em `MP_WEBHOOK_SECRET_AR`.
   - Eventos: `payment.created`, `payment.updated`
   - Guardar **Clave secreta** → `MP_WEBHOOK_SECRET_AR` env

2. **Testar:**
   ```bash
   curl -X POST https://astrodicas.pnzdigital.com.br/api/webhooks/mercadopago/ar/notify \
     -H "x-signature: <signature>" \
     -d '{"data": {"id": "123"}}'
   ```
   Esperado: `401 {"detail": "Assinatura inválida."}` — assinatura falsa recusada prova que a validação está ligada.
   Um `503` significa que a clave secreta não chegou no container.

3. **Troubleshoot:**
   - 401: `MP_WEBHOOK_SECRET_AR` errado (clave de outra aplicação)
   - 503: `MP_WEBHOOK_SECRET_AR` ausente no ambiente
   - 404: URL errada ou DNS não resolve
   - 500: Erro na app; checar logs

### Cakto

1. **Configurar:**
   - Painel > Webhooks > Criar
   - URL: `https://astrodicas.pnzdigital.com.br/api/webhooks/cakto`
   - Guardar **Chave secreta** → `CAKTO_WEBHOOK_SECRET` env

2. **Testar:** Mesmo que MP (mas endpoint `/cakto`)

---

## Verificação Pós-Deploy

### Roteamento (curl)

**Host público (`astrodicas.pnzdigital.com.br`):**
```
/ → 200 (storefront.html)
/es/ → 200 (storefront.html)
/oferta-lua-1 → 200 (lp-plano-lua/v1)
/oferta-lua-2 → 200 (lp-plano-lua/v2)
/es/oferta-lua-1 → 200
/es/oferta-lua-2 → 200
/checkout → 200 (checkout.html)
/obrigado → 200 (obrigado.html)
/admin → 404 ✓ (correto: vendas só em public)
/api/health → 200 JSON
```

**Host dash (`dash.astrodicas.pnzdigital.com.br`):**
```
/ → 200 (index.html — portal logado)
/oferta-lua-1 → 404 ✓ (correto: vendas bloqueadas em dash)
/oferta-lua-2 → 404 ✓
/checkout → 200 (checkout.html)
/admin → 200 (admin.html)
/api/health → 200 JSON
```

### Integração

- [ ] MP: Criar pedido em produção, confirmar webhook recebido (`/api/webhooks/mercadopago/ar/notify` logs)
- [ ] Cakto: Idem
- [ ] E-mail: Confirmar que post-compra chega (Resend logs)
- [ ] Leitura astrológica: Gerar uma, checar que MiniMax ou fallback editorial aparece

---

## Rollback & Troubleshooting

| Sintoma | Causa | Ação |
|---|---|---|
| 500 na API startup | Schema/migrations quebradas | Rollback DB; checar logs para SQL error |
| Webhook MP 401 | Clave de outra aplicação | Conferir `MP_WEBHOOK_SECRET_AR` contra o painel do app argentino |
| Webhook MP 503 | Clave ausente no container | Setar `MP_WEBHOOK_SECRET_AR` no Coolify e redeploy |
| Webhook Cakto 403 | Secret errado/não setado | Confirmar `CAKTO_WEBHOOK_SECRET` no Coolify |
| Cookies de sessão não persistem | `COOKIE_SECURE=1` mas HTTP | Ativar HTTPS no proxy reverso ou setar `COOKIE_SECURE=0` (dev only) |
| `/oferta-lua-*` serve 200 em dash | `ROLE` env incorreto ou nginx config errada | Verificar nginx.runtime.conf mapeamento Host |
| E-mail não chega | `RESEND_API_KEY` vazio ou inválido | Checar API key no Resend; logs da app |
| LLM lento/timeout | MiniMax indisponível; fallback ativo | App continua, usa texto estático; OK |
| 429 MiniMax | Rate limit; esperar ou upgrade plano | Logs da app mostram `429`; retry automático em 10s |

**Logs:**
- API: `docker logs astrodicas-api` (ou Coolify UI)
- Nginx: `docker logs astrodicas-portal` (ou Coolify UI)
- Postgres: `docker logs astrodicas-db`

---

## Configuração Mínima (Coolify)

```yaml
services:
  api:
    image: astrodicas-site-api  # from api/Dockerfile
    env:
      SITE_SECRET_KEY: <secret>
      DATABASE_URL: postgresql+psycopg://astrodicas:***@db:5432/astrodicas
      MP_WEBHOOK_SECRET: <secret>
      CAKTO_WEBHOOK_SECRET: <secret>
    depends_on:
      db:
        condition: service_healthy
    ports:
      - 8000
    healthcheck:
      test: curl http://localhost:8000/api/health

  portal:
    image: astrodicas-site-portal  # from portal-demo/Dockerfile
    depends_on:
      - api
    ports:
      - 80
    environment:
      # Nginx tira Host header; nenhuma env necessária

  db:
    image: postgres:16-alpine
    env:
      POSTGRES_USER: astrodicas
      POSTGRES_PASSWORD: <secret>
      POSTGRES_DB: astrodicas
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: pg_isready -U astrodicas -d astrodicas
```

Deploy: Push Git → Coolify auto-rebuild + up.

---

**Pronto para produção? Checklist:**
- [ ] Variáveis secret setadas no Coolify (não em código)
- [ ] Postgres backup policy configurada
- [ ] TLS habilitado no proxy reverso
- [ ] Webhooks testados com secrets corretos
- [ ] Rotas verificadas (public vs dash behavior)
- [ ] Logs monitorados (alertas em 500/429)
