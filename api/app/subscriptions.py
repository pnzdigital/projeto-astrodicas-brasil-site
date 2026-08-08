"""Diário Astral por assinatura, abrindo com 3 dias grátis sem cartão.

O funil: a pessoa lê o horóscopo do dia grátis (``horoscope_free``), recebe
a oferta e entra com nome + e-mail. O trial nasce localmente — sem MP,
sem cartão — e expira em 3 dias. Se não cancelar antes, ela assina pelo
checkout normal (GG no Brasil, MP na Argentina).

Três decisões que sustentam isso:

1. **Trial sem cartão.** Não há preapproval nem cobrança inicial: o acesso
   nasce e morre pelo ``expires_at`` do entitlement. Disponível em BR e AR.

2. **1 trial por e-mail, para sempre.** Quem já teve um trial não ganha outro,
   mesmo que o primeiro tenha vencido. O guard verifica qualquer subscription
   do produto, independente do status.

3. **Brinde do 1º mês vitalício.** O Mapa Astral Completo (``site:mapa_astral``)
   é concedido UMA vez, quando a primeira cobrança confirma — não durante o
   trial, e não a cada renovação. PDF entregue é da assinante para sempre.
   ``sync_entitlements`` faz essa distinção via ``_had_first_payment``.
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import mercadopago as mp
from . import pricing
from .checkout import portal_url, site_url
from .db import get_db
from .mailer import send_purchase_confirmation, send_trial_started
from .models import Entitlement, Subscription, User, WebhookEvent
from .ratelimit import checkout_rate_limit, webhook_rate_limit
from .security import decode_token, hash_password

logger = logging.getLogger(__name__)
router = APIRouter()

PRODUCT_ID = "site:diario_astral"
TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "3"))

# Mercado Pago -> vocabulário do site. Mantido para o webhook de preapproval,
# que ainda serve assinaturas recorrentes pagas criadas via MP.
PREAPPROVAL_STATUS = {
    "authorized": "active",
    "pending": "pending",
    "paused": "paused",
    "cancelled": "cancelled",
}

MESSAGES: dict[str, dict[str, str]] = {
    "already_subscribed": {
        "pt-BR": "Este e-mail já teve um trial ou assinatura ativa. Cada e-mail tem direito a um trial.",
        "es-AR": "Este e-mail ya tuvo un trial o suscripción activa. Cada e-mail tiene derecho a un trial.",
    },
    "no_subscription": {
        "pt-BR": "Nenhuma assinatura ativa nesta conta.",
        "es-AR": "Ninguna suscripción activa en esta cuenta.",
    },
    "session_required": {
        "pt-BR": "Faça login para continuar.",
        "es-AR": "Iniciá sesión para continuar.",
    },
    "provider_off": {
        "pt-BR": "O meio de pagamento ainda não está habilitado.",
        "es-AR": "El medio de pago todavía no está habilitado.",
    },
    "provider_refused": {
        "pt-BR": "Não foi possível processar o pagamento. Confira os dados e tente de novo.",
        "es-AR": "No pudimos procesar el pago. Revisá los datos e intentá de nuevo.",
    },
}


def message(key: str, locale: str) -> str:
    table = MESSAGES[key]
    return table.get(locale, table["pt-BR"])


class TrialBody(BaseModel):
    """Corpo do trial sem cartão: só nome, e-mail e locale."""

    model_config = {"extra": "ignore"}

    email: EmailStr
    name: str = Field(default="", max_length=160)
    locale: str = Field(default="pt-BR", max_length=10)
    # Campos legados do Payment Brick — ignorados, mantidos para não quebrar
    # clientes antigos que ainda os enviem.
    card_token_id: str = Field(default="", max_length=120)
    token: str = Field(default="", max_length=120)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(moment: datetime | None) -> datetime | None:
    """SQLite devolve datetime ingênuo; comparação com aware explodiria."""
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _authenticated(session: str | None, db: Session, locale: str) -> User:
    payload = decode_token(session or "")
    user = db.get(User, payload["user_id"]) if payload else None
    if not user:
        raise HTTPException(status_code=401, detail=message("session_required", locale))
    return user


def _had_first_payment(subscription: Subscription) -> bool:
    """True se a primeira cobrança já foi confirmada.

    Critério: current_period_end > trial_ends_at. Se não há trial (assinatura
    paga direta), qualquer current_period_end conta como pagamento.
    """
    trial_end = _aware(subscription.trial_ends_at)
    period_end = _aware(subscription.current_period_end)
    if trial_end is None:
        return period_end is not None
    if period_end is None:
        return False
    return period_end > trial_end


def sync_entitlements(db: Session, subscription: Subscription) -> None:
    """Espelha o prazo da assinatura nos entitlements do plano.

    Regras:
    - Produto base (site:diario_astral): expires_at = current_period_end, sempre
      atualizado. Cancelamento não apaga — acesso dura até o fim do período.
    - Bundle items (site:mapa_astral e similares): só após primeira cobrança
      confirmada, sem expires_at (vitalício). Renovações não sobrescrevem o
      expires_at já nulo — PDF entregue é da assinante para sempre.
    - Durante trial (sem cobrança confirmada): apenas o produto base é concedido.
    """
    expires_at = _aware(subscription.current_period_end)
    paid = _had_first_payment(subscription)

    for product_id in pricing.granted_products(subscription.product_id):
        is_bundle_item = product_id != subscription.product_id

        if is_bundle_item and not paid:
            continue  # brinde só após primeira cobrança

        entitlement = db.scalar(
            select(Entitlement).where(
                Entitlement.user_id == subscription.user_id,
                Entitlement.product_id == product_id,
            )
        )
        if entitlement:
            entitlement.status = "available"
            if not is_bundle_item:
                # Produto base: atualiza prazo a cada renovação.
                entitlement.expires_at = expires_at
            # Bundle items vitalícios: expires_at permanece None — não toca.
            continue

        source = "trial" if (not paid and not is_bundle_item) else "site"
        db.add(
            Entitlement(
                user_id=subscription.user_id,
                product_id=product_id,
                status="available",
                source=source,
                expires_at=None if is_bundle_item else expires_at,
            )
        )


def active_subscription(db: Session, user_id: str) -> Subscription | None:
    return db.scalar(
        select(Subscription)
        .where(Subscription.user_id == user_id, Subscription.status.in_(["trialing", "active", "pending"]))
        .order_by(Subscription.created_at.desc())
    )


def subscription_to_dict(subscription: Subscription | None) -> dict | None:
    if not subscription:
        return None
    trial_ends_at = _aware(subscription.trial_ends_at)
    return {
        "id": subscription.id,
        "product_id": subscription.product_id,
        "status": subscription.status,
        "in_trial": bool(trial_ends_at and trial_ends_at > _now() and subscription.status == "trialing"),
        "trial_ends_at": trial_ends_at.isoformat() if trial_ends_at else None,
        "current_period_end": (
            _aware(subscription.current_period_end).isoformat() if subscription.current_period_end else None
        ),
        "cancelled_at": _aware(subscription.cancelled_at).isoformat() if subscription.cancelled_at else None,
        "amount_label": pricing.format_amount(subscription.amount_minor, subscription.currency),
    }


@router.post("/api/trial/start", dependencies=[Depends(checkout_rate_limit)])
def start_trial(body: TrialBody, db: Session = Depends(get_db)) -> dict:
    """Abre 3 dias grátis sem cartão. Cria conta, concede acesso ao horóscopo
    e envia a senha por e-mail. Disponível em BR e AR.

    Só nome + e-mail — sem tokenizar cartão, sem preapproval no MP. O acesso
    vive no entitlement e expira sozinho no fim do trial. A conversão para
    assinatura paga acontece depois, pelo checkout normal.
    """
    locale = pricing.normalize_locale(body.locale)
    email = body.email.lower()

    user = db.scalar(select(User).where(User.email == email))

    # 1 trial por e-mail, para sempre — inclusive trials expirados ou cancelados.
    if user:
        had_any_trial = db.scalar(
            select(Subscription).where(
                Subscription.user_id == user.id,
                Subscription.product_id == PRODUCT_ID,
            )
        )
        if had_any_trial:
            raise HTTPException(status_code=409, detail=message("already_subscribed", locale))

    is_new_account = user is None
    if not user:
        placeholder_password = secrets.token_urlsafe(9)
        user = User(email=email, password_hash=hash_password(placeholder_password), name=body.name, locale=locale)
        db.add(user)
        db.flush()

    trial_ends_at = _now() + timedelta(days=TRIAL_DAYS)
    subscription = Subscription(
        user_id=user.id,
        provider="none",   # sem cartão, sem provedor externo
        external_id=str(uuid4()),
        product_id=PRODUCT_ID,
        status="trialing",
        amount_minor=pricing.amount_minor(PRODUCT_ID, locale),
        currency=pricing.currency_for(locale),
        locale=locale,
        market=pricing.market_for(locale),
        trial_ends_at=trial_ends_at,
        current_period_end=trial_ends_at,
    )
    db.add(subscription)
    db.flush()
    sync_entitlements(db, subscription)

    temp_password = None
    if is_new_account:
        temp_password = secrets.token_urlsafe(9)
        user.password_hash = hash_password(temp_password)

    db.commit()

    # E-mail depois do commit: acesso garantido independente do provedor de e-mail.
    send_trial_started(
        email=user.email,
        name=user.name,
        trial_ends_at=trial_ends_at,
        locale=locale,
        temp_password=temp_password,
        portal_url=portal_url(),
    )

    return {
        "status": "trialing",
        "trial_ends_at": trial_ends_at.isoformat(),
        "subscription": subscription_to_dict(subscription),
    }


@router.get("/api/me/subscription")
def my_subscription(
    request: Request, site_session: str | None = Cookie(default=None), db: Session = Depends(get_db)
) -> dict:
    user = _authenticated(site_session, db, "pt-BR")
    return {"subscription": subscription_to_dict(active_subscription(db, user.id))}


@router.post("/api/me/subscription/cancel")
def cancel_subscription(
    request: Request, site_session: str | None = Cookie(default=None), db: Session = Depends(get_db)
) -> dict:
    user = _authenticated(site_session, db, "pt-BR")
    locale = pricing.normalize_locale(user.locale)
    subscription = active_subscription(db, user.id)
    if not subscription:
        raise HTTPException(status_code=404, detail=message("no_subscription", locale))

    if subscription.external_id and subscription.provider != "none":
        try:
            mp.cancel_preapproval(subscription.external_id)
        except mp.MercadoPagoError as exc:
            # Cancelar é a promessa da landing. Se o provedor está fora do
            # ar, marcamos assim mesmo e reconciliamos pelo webhook — deixar o
            # cliente sem conseguir cancelar é pior do que uma divergência
            # temporária, e o próximo webhook corrige o estado.
            logger.warning("Cancelamento não confirmado no provedor: %s", exc)

    subscription.status = "cancelled"
    subscription.cancelled_at = _now()
    # O acesso continua até o fim do que já foi pago (ou do trial). Cortar na
    # hora seria cobrar por um período e não entregar.
    sync_entitlements(db, subscription)
    db.commit()

    return {
        "status": "cancelled",
        "access_until": (
            _aware(subscription.current_period_end).isoformat() if subscription.current_period_end else None
        ),
        "subscription": subscription_to_dict(subscription),
    }


@router.post("/api/webhooks/mercadopago/ar/subscription", dependencies=[Depends(webhook_rate_limit)])
async def subscription_notification(request: Request, db: Session = Depends(get_db)) -> dict:
    """Notificações da assinatura argentina, assinadas por ``MP_WEBHOOK_SECRET_AR``.

    Dois tipos importam: ``subscription_preapproval`` (o estado da assinatura
    mudou) e ``subscription_authorized_payment`` (uma mensalidade foi cobrada).
    O resto é ruído do painel e responde 200 para o Mercado Pago não ficar
    reenviando.
    """
    payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    data_id = str((payload.get("data") or {}).get("id") or request.query_params.get("data.id") or "")
    topic = payload.get("type") or payload.get("topic") or request.query_params.get("type") or ""
    if not data_id:
        return {"ok": True, "ignored": "sem data.id"}

    if not mp.webhook_secret("MP_WEBHOOK_SECRET_AR"):
        env = os.getenv("ENV", "development")
        if env == "production" or os.getenv("ALLOW_INSECURE_DEV", "0") != "1":
            raise HTTPException(status_code=503, detail="Webhook não configurado.")
    if not mp.verify_signature(
        request.headers.get("x-signature", ""),
        request.headers.get("x-request-id", ""),
        data_id,
        "MP_WEBHOOK_SECRET_AR",
    ):
        raise HTTPException(status_code=401, detail="Assinatura inválida.")

    if topic not in {"subscription_preapproval", "subscription_authorized_payment"}:
        return {"ok": True, "ignored": f"tipo {topic}"}

    # Idempotência: o Mercado Pago reenvia a mesma notificação até receber 200.
    event_id = f"{topic}:{data_id}"
    if db.scalar(select(WebhookEvent).where(WebhookEvent.provider == "mercadopago", WebhookEvent.event_id == event_id)):
        return {"ok": True, "duplicate": True}

    if topic == "subscription_preapproval":
        try:
            preapproval = mp.get_preapproval(data_id)
        except mp.MercadoPagoError:
            raise HTTPException(status_code=502, detail="Não foi possível consultar a assinatura.")
        subscription = db.scalar(
            select(Subscription).where(Subscription.provider == "mercadopago", Subscription.external_id == data_id)
        )
        if not subscription:
            return {"ok": True, "ignored": "assinatura desconhecida"}
        db.add(WebhookEvent(provider="mercadopago", event_id=event_id, payload=_digest(preapproval)))

        mp_status = str(preapproval.get("status") or "")
        just_authorized = mp_status == "authorized" and subscription.status == "pending"
        if just_authorized:
            trial_ends_at = _now() + timedelta(days=TRIAL_DAYS)
            subscription.status = "trialing"
            subscription.trial_ends_at = trial_ends_at
            subscription.current_period_end = trial_ends_at
        else:
            novo = PREAPPROVAL_STATUS.get(mp_status, subscription.status)
            trial_ends_at = _aware(subscription.trial_ends_at)
            if novo == "active" and trial_ends_at and trial_ends_at > _now():
                novo = "trialing"
            subscription.status = novo
            if novo == "cancelled" and not subscription.cancelled_at:
                subscription.cancelled_at = _now()

        already_notified = bool((subscription.raw_payload or {}).get("notified_at"))
        is_new_account = bool((subscription.raw_payload or {}).get("new_account"))
        subscription.raw_payload = {**_digest(preapproval), "new_account": is_new_account}
        sync_entitlements(db, subscription)
        db.commit()

        if just_authorized and not already_notified:
            user = db.get(User, subscription.user_id)
            temp_password = None
            if is_new_account:
                temp_password = secrets.token_urlsafe(9)
                user.password_hash = hash_password(temp_password)
            send_purchase_confirmation(
                email=user.email,
                name=user.name,
                product_title=pricing.title_for(subscription.product_id, subscription.locale),
                amount_label=pricing.format_amount(subscription.amount_minor, subscription.currency),
                locale=subscription.locale,
                temp_password=temp_password,
            )
            subscription.raw_payload = {**subscription.raw_payload, "notified_at": _now().isoformat()}
            db.commit()

        return {"ok": True, "subscription_id": subscription.id, "status": subscription.status}

    # subscription_authorized_payment: a mensalidade foi cobrada.
    try:
        payment = mp.get_payment(data_id)
    except mp.MercadoPagoError:
        raise HTTPException(status_code=502, detail="Não foi possível consultar o pagamento.")
    subscription = db.scalar(
        select(Subscription).where(
            Subscription.provider == "mercadopago",
            Subscription.external_id == str(payment.get("metadata", {}).get("preapproval_id") or payment.get("preapproval_id") or ""),
        )
    )
    if not subscription:
        return {"ok": True, "ignored": "assinatura desconhecida"}

    db.add(WebhookEvent(provider="mercadopago", event_id=event_id, payload={"id": payment.get("id"), "status": payment.get("status")}))
    if mp.internal_status(payment.get("status")) == "paid":
        base = max(_aware(subscription.current_period_end) or _now(), _now())
        subscription.current_period_end = base + timedelta(days=30)
        if subscription.status == "trialing":
            subscription.status = "active"
        sync_entitlements(db, subscription)
    db.commit()
    return {"ok": True, "subscription_id": subscription.id, "status": subscription.status}


def _digest(preapproval: dict) -> dict:
    """Só o necessário: dado de cartão nunca entra no banco do site."""
    return {
        "id": preapproval.get("id"),
        "status": preapproval.get("status"),
        "external_reference": preapproval.get("external_reference"),
        "next_payment_date": preapproval.get("next_payment_date"),
    }
