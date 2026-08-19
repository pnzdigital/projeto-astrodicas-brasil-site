"""Checkout e liberação de acesso do canal site.

Fluxo transparente (Argentina, Mercado Pago):

1. ``POST /api/checkout/order``   abre uma ordem pendente com o preço do servidor;
2. ``POST /api/checkout/payment`` envia o token do Payment Brick ao provedor;
3. ``POST /api/webhooks/mercadopago/notify`` confirma a aprovação assíncrona.

A liberação (`fulfill_order`) é a mesma nos três caminhos e é idempotente:
cria a conta pelo e-mail se ela ainda não existir, concede os produtos do
pacote e dispara o e-mail transacional. Nada aqui conhece o Telegram.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse, urlunparse, parse_qs

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import mercadopago as mp
from . import pricing
from .db import get_db
from .mailer import send_purchase_confirmation
from .models import Entitlement, Order, User, WebhookEvent
from .ratelimit import checkout_rate_limit, webhook_rate_limit
from .security import decode_token, hash_password

logger = logging.getLogger(__name__)
router = APIRouter()

PAID_STATUSES = {"paid", "approved"}

# Produtos cuja compra avulsa (canal BR, GG Checkout / PIX) gera acesso com prazo.
# Ambos liberam site:diario_astral; o prazo é carimbado no entitlement diario_astral.
# Outros produtos (mapas, combos) continuam vitalícios.
TIMED_ACCESS_PRODUCTS = {"site:diario_astral", "site:diario_astral_oferta_saida"}
DIARIO_ASTRAL_PRODUCT_ID = "site:diario_astral"
DIARIO_ASTRAL_ACCESS_DAYS = 30


class OrderBody(BaseModel):
    product_id: str = Field(max_length=120)
    email: EmailStr | None = None
    name: str = Field(default="", max_length=160)
    locale: str = Field(default="pt-BR", max_length=10)
    # Quando True, o servidor usa o e-mail da sessão autenticada e ignora o campo
    # email do corpo — o cliente não pode forjar o e-mail da conta.
    use_session: bool = False
    # De onde a cliente veio. O navegador manda o que guardou (ver attribution.js
    # nas páginas): a PRIMEIRA visita marcada e a ÚLTIMA antes da compra.
    # Tolerante de propósito — origem faltando ou torta nunca pode impedir uma
    # venda de acontecer, então nada aqui é obrigatório e tudo é truncado.
    attribution: dict = Field(default_factory=dict)


# Chaves aceitas do corpo, e o tamanho máximo de cada uma. Truncar em vez de
# recusar: um link com utm_campaign gigante é erro de quem montou o link, e não
# pode custar a venda.
_ATTR_CAMPOS: dict[str, int] = {
    "first_source": 80, "first_medium": 80, "first_campaign": 120, "first_content": 120,
    "last_source": 80, "last_medium": 80, "last_campaign": 120, "last_content": 120,
    "landing_page": 300, "referrer": 300,
}


def normaliza_atribuicao(bruto: dict | None) -> dict[str, str]:
    """Limpa o que o navegador mandou e devolve as colunas do pedido.

    Origem vira minúscula e sem espaço nas pontas porque relatório é GROUP BY:
    "Instagram", "instagram " e "instagram" seriam três linhas diferentes no
    painel, e aí o número que a dona olha para decidir onde investir está errado.
    landing_page e referrer preservam caixa — são URLs, e URL diferencia
    maiúscula de minúscula no caminho.
    """
    bruto = bruto or {}
    limpo: dict[str, str] = {}
    for campo, limite in _ATTR_CAMPOS.items():
        valor = bruto.get(campo)
        if not isinstance(valor, str):
            continue
        valor = valor.strip()[:limite]
        if campo not in ("landing_page", "referrer"):
            valor = valor.lower()
        limpo[campo] = valor
    return limpo


class PaymentBody(BaseModel):
    order_id: str = Field(max_length=36)
    form_data: dict


def site_url() -> str:
    return os.getenv("SITE_PUBLIC_URL", "https://astrodicas.pnzdigital.com.br").rstrip("/")


def portal_url() -> str:
    return os.getenv("PORTAL_URL", "https://dash.astrodicas.pnzdigital.com.br/")


def gg_checkout_url(product_id: str) -> str | None:
    """URL de checkout do GG para o produto, ou None se não configurada.

    Configurar: GG_CHECKOUT_URLS='{"site:diario_astral": "https://checkout.gg.com/abc"}'
    Adicionar produto: só editar a env var, sem redeploy.
    """
    raw = os.getenv("GG_CHECKOUT_URLS", "").strip()
    if not raw:
        return None
    try:
        urls = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("GG_CHECKOUT_URLS não é JSON válido — nenhuma URL GG disponível")
        return None
    url = (urls.get(product_id) or "").strip()
    return url if url else None


def _append_checkout_params(base_url: str, email: str, order_id: str) -> str:
    """Acrescenta email e ref de pedido ao link de checkout externo (para prefill)."""
    parsed = urlparse(base_url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs.setdefault("email", [email])
    qs.setdefault("ref", [order_id])
    return urlunparse(parsed._replace(query=urlencode({k: v[0] for k, v in qs.items()})))


# Quem cobra em cada mercado é configuração, não regra de negócio. O padrão
# é Argentina no Mercado Pago e Brasil no GG Checkout (PIX/cartão nacional).
#
# Trocar o Brasil para "mercadopago" exige credenciais de uma conta brasileira
# (MLB): contas do Mercado Pago são presas ao país, e a conta argentina em uso
# hoje (MLA) não cobra em BRL. Sem MP_ACCESS_TOKEN válido para o país, o
# checkout responde 503 em vez de vender.
PROVIDERS = {"mercadopago", "cakto", "ggcheckout"}
DEFAULT_PROVIDERS = {"AR": "mercadopago", "BR": "ggcheckout"}


def provider_for(locale: str | None) -> str:
    """O meio de pagamento configurado para o mercado do locale."""
    market = pricing.market_for(locale)
    padrao = DEFAULT_PROVIDERS.get(market, "cakto")
    escolhido = os.getenv(f"CHECKOUT_PROVIDER_{market}", "").strip().lower()
    if not escolhido:
        return padrao
    if escolhido not in PROVIDERS:
        logger.warning(
            "CHECKOUT_PROVIDER_%s=%r não é um provedor conhecido (%s); usando %s.",
            market,
            escolhido,
            ", ".join(sorted(PROVIDERS)),
            padrao,
        )
        return padrao
    return escolhido


def _checkout_config(locale: str) -> dict:
    """O que o frontend precisa saber para desenhar o pagamento.

    ``transparent`` diz se o cartão é capturado dentro do site (Mercado Pago)
    ou se o cliente sai para um link externo (Cakto). Isso segue o provedor,
    não o país: quando o Brasil for configurado no Mercado Pago, o checkout
    brasileiro passa a ser transparente sozinho.
    """
    provider = provider_for(locale)
    if provider == "mercadopago":
        return {
            "provider": provider,
            "transparent": False,
            "redirect": True,
            "public_key": mp.public_key(),
            "enabled": mp.is_enabled(),
        }
    return {"provider": provider, "transparent": False, "redirect": True, "public_key": "", "enabled": True}


@router.get("/api/catalog")
def catalog(locale: str = "pt-BR") -> dict:
    """Catálogo com o preço oficial do mercado pedido."""
    locale = pricing.normalize_locale(locale)
    return {
        "locale": locale,
        "market": pricing.market_for(locale),
        "currency": pricing.currency_for(locale),
        "products": pricing.catalog(locale),
        "checkout": _checkout_config(locale),
    }


@router.post("/api/checkout/order", dependencies=[Depends(checkout_rate_limit)])
def open_order(
    body: OrderBody,
    db: Session = Depends(get_db),
    site_session: str | None = Cookie(default=None),
) -> dict:
    if not pricing.is_known_product(body.product_id):
        raise HTTPException(status_code=404, detail="Produto não encontrado.")

    # Resolução de identidade: sessão autenticada tem precedência quando solicitado.
    if body.use_session:
        payload = decode_token(site_session or "")
        if not payload:
            raise HTTPException(status_code=401, detail="Sessão inválida ou expirada. Faça login novamente.")
        session_user = db.get(User, payload["user_id"])
        if not session_user:
            raise HTTPException(status_code=401, detail="Sessão inválida ou expirada. Faça login novamente.")
        resolved_email = session_user.email
        resolved_name = body.name.strip() or session_user.name or ""
    else:
        if not body.email:
            raise HTTPException(status_code=422, detail="E-mail obrigatório para compra sem sessão.")
        resolved_email = str(body.email).lower()
        resolved_name = body.name.strip()
    locale = pricing.normalize_locale(body.locale)
    provider = provider_for(locale)
    if provider == "mercadopago" and not mp.is_enabled():
        raise HTTPException(status_code=503, detail="El medio de pago todavía no está habilitado.")
    minor = pricing.amount_minor(body.product_id, locale)
    order = Order(
        provider=provider,
        external_id="",
        product_id=body.product_id,
        status="pending",
        amount_minor=minor,
        currency=pricing.currency_for(locale),
        locale=locale,
        market=pricing.market_for(locale),
        customer_email=resolved_email,
        raw_payload={"name": resolved_name},
        **normaliza_atribuicao(body.attribution),
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    # `external_id` é único por provedor: sem ele, duas ordens pendentes colidiriam.
    order.external_id = order.id
    db.commit()

    init_point = ""
    if provider == "ggcheckout":
        gg_url = gg_checkout_url(body.product_id)
        if not gg_url:
            logger.error(
                "URL GG Checkout não configurada para product_id=%r; configure GG_CHECKOUT_URLS",
                body.product_id,
            )
            raise HTTPException(
                status_code=503,
                detail="O checkout deste produto ainda não está disponível. Entre em contato pelo suporte.",
            )
        init_point = _append_checkout_params(gg_url, resolved_email, order.id)
    elif provider == "mercadopago":
        try:
            preference = mp.create_preference(
                order_id=order.id,
                title=pricing.title_for(order.product_id, locale),
                amount=round(minor / 100, 2),
                currency=order.currency,
                payer_email=order.customer_email or "",
                success_url=f"{site_url()}/obrigado?order_id={order.id}",
                failure_url=f"{site_url()}/checkout?order_id={order.id}&status=failure",
                pending_url=f"{site_url()}/checkout?order_id={order.id}&status=pending",
                notification_url=f"{site_url()}/api/webhooks/mercadopago/ar/notify",
            )
        except mp.MercadoPagoError as exc:
            logger.warning("Preferência recusada pelo provedor: %s", exc)
            raise HTTPException(status_code=502, detail="No pudimos iniciar el pago. Probá de nuevo.") from exc
        order.raw_payload = {**(order.raw_payload or {}), "preference_id": preference.get("id")}
        db.commit()
        init_point = preference.get("init_point") or preference.get("sandbox_init_point") or ""

    return {
        "order_id": order.id,
        "product_id": order.product_id,
        "title": pricing.title_for(order.product_id, locale),
        "amount": round(minor / 100, 2),
        "amount_minor": minor,
        "currency": order.currency,
        "price_label": pricing.format_amount(minor, order.currency),
        "locale": locale,
        "public_key": mp.public_key(),
        "init_point": init_point,
        "redirect": bool(init_point),
    }


@router.get("/api/checkout/order/{order_id}/conversion", dependencies=[Depends(checkout_rate_limit)])
def purchase_conversion(order_id: str, db: Session = Depends(get_db)) -> dict:
    """Retorna somente os dados não pessoais de uma compra confirmada.

    A página de obrigado usa este endpoint como fonte de verdade antes de
    disparar o evento manual ``Purchase`` do Meta Pixel. Ordens inexistentes e
    ainda não pagas têm a mesma resposta para não expor seu estado.
    """
    order = db.get(Order, order_id) if len(order_id) <= 36 else None
    if not order or order.status not in PAID_STATUSES:
        raise HTTPException(status_code=404, detail="Compra confirmada não encontrada.")
    return {
        "event_id": f"site-purchase-{order.id}",
        "order_id": order.id,
        "content_ids": [order.product_id],
        "content_name": pricing.title_for(order.product_id, order.locale),
        "content_type": "product",
        "num_items": 1,
        "value": round(order.amount_minor / 100, 2),
        "currency": order.currency,
        "content_language": order.locale,
    }


@router.post("/api/checkout/payment")
def pay(body: PaymentBody, db: Session = Depends(get_db)) -> dict:
    """Rota do checkout transparente, aposentada em favor do Checkout Pro.

    Mantida viva (em vez de apagada) porque um cliente antigo com a página do
    Payment Brick ainda aberta pode chamar esta rota — ela responde de forma
    clara em vez de sumir com um 404. ``POST /api/checkout/order`` já devolve
    ``init_point`` para o novo fluxo.
    """
    raise HTTPException(
        status_code=410,
        detail="Este medio de pago fue reemplazado. Volvé a iniciar la compra para ser redirigido al checkout.",
    )


@router.post("/api/webhooks/mercadopago/ar/notify", dependencies=[Depends(webhook_rate_limit)])
async def mercadopago_notification_ar(request: Request, db: Session = Depends(get_db)) -> dict:
    """Rota da aplicação argentina, assinada por ``MP_WEBHOOK_SECRET_AR``.

    O Mercado Pago só atende a Argentina neste site (o Brasil vai por Cakto),
    mas cada aplicação do Mercado Pago tem clave secreta própria: uma rota por
    aplicação permite trocar ou revogar o segredo de um mercado sem derrubar
    os outros.
    """
    return await _mercadopago_notification(request, db, env_var="MP_WEBHOOK_SECRET_AR")


@router.post("/api/webhooks/mercadopago/notify", dependencies=[Depends(webhook_rate_limit)])
async def mercadopago_notification(request: Request, db: Session = Depends(get_db)) -> dict:
    """Rota legada, mantida viva de propósito.

    Pagamentos abertos antes do deploy carregam esta URL gravada no próprio
    ``notification_url``: o Mercado Pago vai continuar notificando aqui por
    dias. Desligar esta rota deixaria esses pedidos pagos sem liberação.
    """
    return await _mercadopago_notification(request, db, env_var="MP_WEBHOOK_SECRET")


async def _mercadopago_notification(request: Request, db: Session, env_var: str) -> dict:
    """Notificação oficial do Mercado Pago (payload ``{type, data:{id}}``)."""
    payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    data_id = str((payload.get("data") or {}).get("id") or request.query_params.get("data.id") or "")
    topic = payload.get("type") or payload.get("topic") or request.query_params.get("type") or ""
    if not data_id:
        return {"ok": True, "ignored": "sem data.id"}
    if not mp.webhook_secret(env_var):
        env = os.getenv("ENV", "development")
        allow_insecure = os.getenv("ALLOW_INSECURE_DEV", "0") == "1"
        if env == "production" or not allow_insecure:
            raise HTTPException(
                status_code=503,
                detail="Webhook do Mercado Pago indisponível: segredo não configurado.",
            )
    elif not mp.verify_signature(
        request.headers.get("x-signature", ""),
        request.headers.get("x-request-id", ""),
        data_id,
        env_var,
    ):
        raise HTTPException(status_code=401, detail="Assinatura inválida.")
    if topic and topic not in {"payment", "payment.updated", "payment.created", "merchant_order"}:
        return {"ok": True, "ignored": topic}

    event_id = f"mp:{topic or 'payment'}:{data_id}"
    if db.scalar(select(WebhookEvent).where(WebhookEvent.provider == "mercadopago", WebhookEvent.event_id == event_id)):
        return {"ok": True, "duplicate": True}

    if topic == "merchant_order":
        try:
            merchant_order = mp.get_merchant_order(data_id)
        except mp.MercadoPagoError as exc:
            raise HTTPException(status_code=502, detail="Não foi possível consultar a ordem.") from exc

        db.add(WebhookEvent(provider="mercadopago", event_id=event_id, payload=_merchant_order_digest(merchant_order)))
        order = (
            db.get(Order, str(merchant_order.get("external_reference") or ""))
            if merchant_order.get("external_reference")
            else None
        )
        if not order:
            db.commit()
            return {"ok": True, "ignored": "ordem desconhecida"}

        payments = merchant_order.get("payments") or []
        order.status = _merchant_order_status(payments)
        if payments:
            order.external_id = str(payments[0].get("id") or order.external_id)
        order.raw_payload = {**(order.raw_payload or {}), "merchant_order": _merchant_order_digest(merchant_order)}
        db.commit()
        if order.status == "paid":
            fulfill_order(db, order)
        return {"ok": True, "order_id": order.id, "status": order.status}

    try:
        payment = mp.get_payment(data_id)
    except mp.MercadoPagoError as exc:
        raise HTTPException(status_code=502, detail="Não foi possível consultar o pagamento.") from exc

    db.add(WebhookEvent(provider="mercadopago", event_id=event_id, payload=_payment_digest(payment)))
    order = db.get(Order, str(payment.get("external_reference") or "")) if payment.get("external_reference") else None
    if not order:
        db.commit()
        return {"ok": True, "ignored": "ordem desconhecida"}

    order.status = mp.internal_status(payment.get("status"))
    order.external_id = str(payment.get("id") or order.external_id)
    order.raw_payload = {**(order.raw_payload or {}), "payment": _payment_digest(payment)}
    db.commit()
    if order.status == "paid":
        fulfill_order(db, order)
    return {"ok": True, "order_id": order.id, "status": order.status}


def fulfill_order(db: Session, order: Order) -> User:
    """Cria a conta se preciso, libera os produtos e avisa o cliente. Idempotente."""
    email = (order.customer_email or "").lower()
    user = db.scalar(select(User).where(User.email == email)) if email else None
    temp_password = ""
    order_name = ((order.raw_payload or {}).get("name") or "").strip()
    if not user:
        temp_password = secrets.token_urlsafe(9)
        user = User(
            email=email,
            password_hash=hash_password(temp_password),
            name=order_name,
            locale=order.locale,
        )
        db.add(user)
        db.flush()
        # Marca que a conta nasceu desta compra. É o que permite reemitir a
        # senha num reenvio (ver abaixo) sem risco de estourar a senha de
        # alguém que já era cliente.
        order.raw_payload = {**(order.raw_payload or {}), "account_created": True}
    elif (order.raw_payload or {}).get("account_created") and not (order.raw_payload or {}).get("notified_at"):
        # Retentativa de uma compra cuja conta foi criada aqui e cujo e-mail
        # nunca saiu: a senha temporária da primeira passagem morreu na
        # variável local, então reenviar sem ela entregaria uma confirmação
        # sem credencial — a cliente pagou e continuaria de fora. Reemite.
        # Seguro: sem aquele e-mail ela nunca teve como entrar, logo não há
        # senha escolhida por ela para sobrescrever.
        temp_password = secrets.token_urlsafe(9)
        user.password_hash = hash_password(temp_password)
        logger.warning("Reemitindo senha temporária para user=%s order=%s", user.id, order.id)
        if order_name and not user.name:
            user.name = order_name
    elif order_name and not user.name:
        # Preenche nome só se estiver vazio — nunca sobrescreve dado existente.
        user.name = order_name

    granted_now = []
    timed = order.product_id in TIMED_ACCESS_PRODUCTS
    for product_id in pricing.granted_products(order.product_id):
        is_bonus_item = product_id != order.product_id
        policy = pricing.bonus_policy(order.product_id, product_id) if is_bonus_item else "always"
        entitlement = db.scalar(
            select(Entitlement).where(Entitlement.user_id == user.id, Entitlement.product_id == product_id)
        )

        # Prazo de 30 dias só para o entitlement site:diario_astral quando a compra
        # é de um produto avulso com prazo. Estende a partir do vencimento atual
        # se ainda ativo, para não cortar dias já pagos.
        new_expires_at = None
        if timed and product_id == DIARIO_ASTRAL_PRODUCT_ID:
            now = datetime.now(timezone.utc)
            if entitlement and entitlement.expires_at is not None:
                current = entitlement.expires_at
                if current.tzinfo is None:
                    current = current.replace(tzinfo=timezone.utc)
                base = max(current, now)
            else:
                base = now
            new_expires_at = base + timedelta(days=DIARIO_ASTRAL_ACCESS_DAYS)

        if entitlement:
            if policy == "once":
                # Já teve este entitlement antes — ativo, revogado ou expirado
                # não importa. Brinde de uma vez só não reconcede nem
                # reenfileira geração numa renovação/segunda compra.
                continue
            if entitlement.status != "available":
                entitlement.status = "available"
                granted_now.append(product_id)
            if new_expires_at is not None:
                entitlement.expires_at = new_expires_at
            continue
        db.add(Entitlement(
            user_id=user.id,
            product_id=product_id,
            status="available",
            source="site",
            expires_at=new_expires_at,
        ))
        granted_now.append(product_id)

    already_notified = bool((order.raw_payload or {}).get("notified_at"))
    order.user_id = user.id
    db.commit()

    if granted_now:
        # Late import: main.py importa checkout no topo do módulo, então
        # checkout não pode importar main em nível de módulo (circular) —
        # mesmo padrão já usado por worker.run_job para chamar de volta em main.
        from .main import enqueue_map_generation

        try:
            enqueue_map_generation(db, user, granted_now)
        except Exception:
            # Entitlement já está liberado e committado acima — falha aqui não
            # pode reverter a compra. Fica só o log; o clique manual no card
            # ainda funciona como caminho de recuperação.
            logger.exception("enqueue_map_generation falhou para order=%s user=%s", order.id, user.id)

    if not already_notified:
        delivery = send_purchase_confirmation(
            email=user.email,
            name=user.name,
            product_title=pricing.title_for(order.product_id, order.locale),
            amount_label=pricing.format_amount(order.amount_minor, order.currency),
            locale=order.locale,
            temp_password=temp_password or None,
        )
        if delivery.get("sent"):
            order.raw_payload = {
                **(order.raw_payload or {}),
                "notified_at": datetime.now(timezone.utc).isoformat(),
            }
            db.commit()
        else:
            # Não marca notified_at: deixa em aberto para a próxima chamada de
            # fulfill_order (MP dispara webhook "payment" e "merchant_order"
            # separados para a mesma compra — a segunda tenta reenviar).
            logger.error(
                "Compra MP confirmada sem e-mail entregue: user=%s product=%s erro=%s",
                user.id,
                order.product_id,
                delivery.get("error"),
            )
    return user


# GG Checkout (Brasil) manda um único webhook por compra — sem o par
# "payment"/"merchant_order" do Mercado Pago não há segunda passagem natural
# por fulfill_order. Se o SMTP falhar naquele instante, a cliente paga e
# nunca recebe a senha, e como a conta só nasce pela compra ela fica de fora
# para sempre. Este cron cobre os dois provedores (filtra por status="paid"),
# mas hoje só o BR precisa dele de verdade.
RESEND_MIN_AGE_MINUTES = 10
RESEND_MAX_ATTEMPTS = 5
# Teto de idade: a varredura só olha compras recentes. Sem isso ela alcançaria
# o histórico inteiro — inclusive Orders anteriores a `notified_at` existir,
# que não têm o campo simplesmente porque ninguém gravava. Essas clientes
# receberiam, do nada, um e-mail de uma compra de meses atrás.
RESEND_MAX_AGE_HOURS = 72


def run_resend_purchase_emails(db: Session, now: datetime | None = None) -> dict:
    """Reenvia o e-mail de compra de Orders pagas sem `notified_at`.

    Idempotente: fulfill_order só marca notified_at após confirmação real de
    envio, então rodar isto de novo não duplica e-mail para quem já foi
    entregue. `notify_attempts` limita tentativas (SMTP fora do ar não pode
    virar loop infinito de reenvio). `RESEND_MIN_AGE_MINUTES` dá tempo para o
    webhook original terminar antes do cron pegar a mesma Order.
    """
    now = now or datetime.now(timezone.utc)
    stats = {"resent": 0, "skipped_notified": 0, "skipped_too_recent": 0, "skipped_limit": 0, "errors": 0}

    floor = now - timedelta(hours=RESEND_MAX_AGE_HOURS)
    orders = db.scalars(
        select(Order).where(Order.status.in_(PAID_STATUSES), Order.created_at >= floor)
    ).all()
    for order in orders:
        payload = order.raw_payload or {}
        if payload.get("notified_at"):
            stats["skipped_notified"] += 1
            continue

        created_at = order.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if now - created_at < timedelta(minutes=RESEND_MIN_AGE_MINUTES):
            stats["skipped_too_recent"] += 1
            continue

        attempts = int(payload.get("notify_attempts") or 0)
        if attempts >= RESEND_MAX_ATTEMPTS:
            stats["skipped_limit"] += 1
            continue

        order.raw_payload = {**payload, "notify_attempts": attempts + 1}
        db.commit()
        try:
            fulfill_order(db, order)
        except Exception:
            logger.exception("run_resend_purchase_emails falhou para order=%s", order.id)
            stats["errors"] += 1
            continue

        db.refresh(order)
        if (order.raw_payload or {}).get("notified_at"):
            stats["resent"] += 1

    return stats


@router.post("/api/tasks/resend-purchase-emails")
async def resend_purchase_emails_task(request: Request, db: Session = Depends(get_db)) -> dict:
    """Cron para reentregar e-mail de compra que ficou sem confirmação de envio.

    Configurar no Coolify: cron a cada ~15min, header x-task-secret. Cobre o
    caso GG Checkout (BR) sem retentativa de webhook — ver comentário acima
    de run_resend_purchase_emails.
    """
    secret = os.getenv("TASK_SECRET", "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="TASK_SECRET não configurado.")
    provided = request.headers.get("x-task-secret", "")
    if not hmac.compare_digest(secret.encode(), provided.encode()):
        raise HTTPException(status_code=401, detail="Segredo inválido.")

    stats = run_resend_purchase_emails(db)
    return {"ok": True, **stats}


def _merchant_order_status(payments: list) -> str:
    """Uma ordem do Checkout Pro pode ter vários pagamentos (retentativas do
    cliente); um único aprovado já libera a compra."""
    statuses = [p.get("status") for p in payments]
    if any(status == "approved" for status in statuses):
        return "paid"
    if any(status == "rejected" for status in statuses):
        return "failed"
    return "pending"


def _merchant_order_digest(merchant_order: dict) -> dict:
    return {
        "id": merchant_order.get("id"),
        "order_status": merchant_order.get("order_status"),
        "external_reference": merchant_order.get("external_reference"),
        "payments": [
            {"id": p.get("id"), "status": p.get("status")} for p in (merchant_order.get("payments") or [])
        ],
    }


def _payment_digest(payment: dict) -> dict:
    """Guarda só o necessário: dados de cartão nunca entram no banco do site."""
    return {
        "id": payment.get("id"),
        "status": payment.get("status"),
        "status_detail": payment.get("status_detail"),
        "payment_method_id": payment.get("payment_method_id"),
        "payment_type_id": payment.get("payment_type_id"),
        "transaction_amount": payment.get("transaction_amount"),
        "currency_id": payment.get("currency_id"),
        "date_approved": payment.get("date_approved"),
        "external_reference": payment.get("external_reference"),
    }
