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

import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse, urlunparse, parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import mercadopago as mp
from . import pricing
from .db import get_db
from .mailer import send_purchase_confirmation
from .models import Entitlement, Order, User, WebhookEvent
from .ratelimit import checkout_rate_limit, webhook_rate_limit
from .security import hash_password

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
    email: EmailStr
    name: str = Field(default="", max_length=160)
    locale: str = Field(default="pt-BR", max_length=10)


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
def open_order(body: OrderBody, db: Session = Depends(get_db)) -> dict:
    if not pricing.is_known_product(body.product_id):
        raise HTTPException(status_code=404, detail="Produto não encontrado.")
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
        customer_email=body.email.lower(),
        raw_payload={"name": body.name},
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
        init_point = _append_checkout_params(gg_url, body.email.lower(), order.id)
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
    elif order_name and not user.name:
        # Preenche nome só se estiver vazio — nunca sobrescreve dado existente.
        user.name = order_name

    granted_now = []
    timed = order.product_id in TIMED_ACCESS_PRODUCTS
    for product_id in pricing.granted_products(order.product_id):
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
    if not already_notified:
        order.raw_payload = {
            **(order.raw_payload or {}),
            "notified_at": datetime.now(timezone.utc).isoformat(),
        }
    db.commit()

    if not already_notified:
        delivery = send_purchase_confirmation(
            email=user.email,
            name=user.name,
            product_title=pricing.title_for(order.product_id, order.locale),
            amount_label=pricing.format_amount(order.amount_minor, order.currency),
            locale=order.locale,
            temp_password=temp_password or None,
        )
        if not delivery.get("sent"):
            logger.error(
                "Compra MP confirmada sem e-mail entregue: user=%s product=%s erro=%s",
                user.id,
                order.product_id,
                delivery.get("error"),
            )
    return user


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
