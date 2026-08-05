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

import logging
import os
import secrets
from datetime import datetime, timezone

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


@router.get("/api/catalog")
def catalog(locale: str = "pt-BR") -> dict:
    """Catálogo com o preço oficial do mercado pedido."""
    locale = pricing.normalize_locale(locale)
    return {
        "locale": locale,
        "market": pricing.market_for(locale),
        "currency": pricing.currency_for(locale),
        "products": pricing.catalog(locale),
        "checkout": {
            "provider": "mercadopago" if pricing.market_for(locale) == "AR" else "cakto",
            "transparent": pricing.market_for(locale) == "AR",
            "public_key": mp.public_key() if pricing.market_for(locale) == "AR" else "",
            "enabled": mp.is_enabled() if pricing.market_for(locale) == "AR" else True,
        },
    }


@router.post("/api/checkout/order", dependencies=[Depends(checkout_rate_limit)])
def open_order(body: OrderBody, db: Session = Depends(get_db)) -> dict:
    if not pricing.is_known_product(body.product_id):
        raise HTTPException(status_code=404, detail="Produto não encontrado.")
    locale = pricing.normalize_locale(body.locale)
    market = pricing.market_for(locale)
    if market == "AR" and not mp.is_enabled():
        raise HTTPException(status_code=503, detail="El medio de pago todavía no está habilitado.")
    minor = pricing.amount_minor(body.product_id, locale)
    order = Order(
        provider="mercadopago" if market == "AR" else "cakto",
        external_id="",
        product_id=body.product_id,
        status="pending",
        amount_minor=minor,
        currency=pricing.currency_for(locale),
        locale=locale,
        market=market,
        customer_email=body.email.lower(),
        raw_payload={"name": body.name},
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    # `external_id` é único por provedor: sem ele, duas ordens pendentes colidiriam.
    order.external_id = order.id
    db.commit()
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
    order = db.get(Order, body.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Orden no encontrada.")
    if order.status in PAID_STATUSES:
        return {"status": "approved", "order_id": order.id, "portal_url": portal_url()}
    if not body.form_data.get("payment_method_id"):
        raise HTTPException(status_code=400, detail="Datos de pago incompletos.")

    payer = body.form_data.get("payer") or {}
    payer_email = (payer.get("email") or order.customer_email or "").strip().lower()
    if not payer_email:
        raise HTTPException(status_code=400, detail="Falta el email del comprador.")

    try:
        payment = mp.create_payment(
            amount=round(order.amount_minor / 100, 2),
            description=pricing.title_for(order.product_id, order.locale),
            order_id=order.id,
            payer_email=payer_email,
            form_data=body.form_data,
            notification_url=f"{site_url()}/api/webhooks/mercadopago/notify",
        )
    except mp.MercadoPagoError as exc:
        logger.warning("Pagamento recusado pelo provedor: %s", exc)
        raise HTTPException(status_code=502, detail="No pudimos procesar el pago. Probá con otro medio.") from exc

    status = mp.internal_status(payment.get("status"))
    order.customer_email = payer_email
    order.external_id = str(payment.get("id") or order.id)
    order.raw_payload = {**(order.raw_payload or {}), "payment": _payment_digest(payment)}
    order.status = status
    db.commit()

    if status == "paid":
        fulfill_order(db, order)

    transaction = payment.get("transaction_details") or {}
    interaction = (payment.get("point_of_interaction") or {}).get("transaction_data") or {}
    return {
        "status": payment.get("status"),
        "detail": payment.get("status_detail"),
        "order_id": order.id,
        "approved": status == "paid",
        "portal_url": portal_url() if status == "paid" else "",
        "ticket_url": transaction.get("external_resource_url") or interaction.get("ticket_url"),
    }


@router.post("/api/webhooks/mercadopago/notify", dependencies=[Depends(webhook_rate_limit)])
async def mercadopago_notification(request: Request, db: Session = Depends(get_db)) -> dict:
    """Notificação oficial do Mercado Pago (payload ``{type, data:{id}}``)."""
    payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    data_id = str((payload.get("data") or {}).get("id") or request.query_params.get("data.id") or "")
    topic = payload.get("type") or payload.get("topic") or request.query_params.get("type") or ""
    if not data_id:
        return {"ok": True, "ignored": "sem data.id"}
    if not os.getenv("MP_WEBHOOK_SECRET", "").strip():
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
    ):
        raise HTTPException(status_code=401, detail="Assinatura inválida.")
    if topic and topic not in {"payment", "payment.updated", "payment.created"}:
        return {"ok": True, "ignored": topic}

    event_id = f"mp:{data_id}"
    if db.scalar(select(WebhookEvent).where(WebhookEvent.provider == "mercadopago", WebhookEvent.event_id == event_id)):
        return {"ok": True, "duplicate": True}

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
    if not user:
        temp_password = secrets.token_urlsafe(9)
        user = User(
            email=email,
            password_hash=hash_password(temp_password),
            name=(order.raw_payload or {}).get("name") or "",
            locale=order.locale,
        )
        db.add(user)
        db.flush()

    granted_now = []
    for product_id in pricing.granted_products(order.product_id):
        entitlement = db.scalar(
            select(Entitlement).where(Entitlement.user_id == user.id, Entitlement.product_id == product_id)
        )
        if entitlement:
            if entitlement.status != "available":
                entitlement.status = "available"
                granted_now.append(product_id)
            continue
        db.add(Entitlement(user_id=user.id, product_id=product_id, status="available", source="site"))
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
        send_purchase_confirmation(
            email=user.email,
            name=user.name,
            product_title=pricing.title_for(order.product_id, order.locale),
            amount_label=pricing.format_amount(order.amount_minor, order.currency),
            locale=order.locale,
            temp_password=temp_password or None,
        )
    return user


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
