"""Plano Lua por assinatura, abrindo com 3 dias grátis.

O funil: a pessoa cai do anúncio, lê o horóscopo do dia grátis
(``horoscope_free``) e recebe a oferta de 3 dias de horóscopo diário sem pagar,
cadastrando o cartão. No fim dos 3 dias vira Plano Lua mensal, e ela cancela
pelo painel quando quiser.

Três decisões que sustentam isso:

1. **Só Argentina, por enquanto.** Trial com cartão exige assinatura recorrente,
   e a única conta recorrente que existe é a do Mercado Pago (MLA). O Brasil vai
   por Cakto, que aqui é link externo e não expõe recorrência — pedir cartão em
   pt-BR seria prometer um fluxo que o backend não tem. A rota recusa com 409.

2. **O período grátis é do provedor.** ``free_trial`` do Mercado Pago autoriza o
   cartão hoje e cobra só no 4º dia. Contar os dias do nosso lado exigiria que a
   gente disparasse a primeira cobrança sozinho — a parte que não pode falhar.

3. **Quem manda no acesso é o entitlement, não a assinatura.** O portal já
   pergunta ao entitlement, e ele agora vence (``entitlements.py``). A assinatura
   só empurra ``expires_at`` para frente a cada renovação paga. Se o webhook
   parar de chegar, o acesso fecha sozinho no fim do período — falha fechada, que
   é o lado certo para errar.
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import mercadopago as mp
from . import pricing
from .checkout import portal_url, site_url
from .db import get_db
from .mailer import send_purchase_confirmation
from .models import Entitlement, Subscription, User, WebhookEvent
from .ratelimit import checkout_rate_limit, webhook_rate_limit
from .security import decode_token, hash_password

logger = logging.getLogger(__name__)
router = APIRouter()

PRODUCT_ID = "site:plano_lua"
TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "3"))

# Mercado Pago -> vocabulário do site. ``authorized`` cobre tanto o trial quanto
# a assinatura já cobrando; quem separa os dois é ``trial_ends_at``.
PREAPPROVAL_STATUS = {
    "authorized": "active",
    "pending": "pending",
    "paused": "paused",
    "cancelled": "cancelled",
}

MESSAGES: dict[str, dict[str, str]] = {
    "trial_unavailable": {
        "pt-BR": "O teste grátis com cartão ainda não está disponível no Brasil. Assine o Plano Lua pelo checkout.",
        "es-AR": "La prueba gratis con tarjeta todavía no está disponible en tu país.",
    },
    "provider_off": {
        "pt-BR": "O meio de pagamento ainda não está habilitado.",
        "es-AR": "El medio de pago todavía no está habilitado.",
    },
    "card_required": {
        "pt-BR": "Precisamos dos dados do cartão para reservar seu teste grátis.",
        "es-AR": "Necesitamos los datos de la tarjeta para reservar tu prueba gratis.",
    },
    "provider_refused": {
        "pt-BR": "Não foi possível autorizar o cartão. Confira os dados e tente de novo.",
        "es-AR": "No pudimos autorizar la tarjeta. Revisá los datos e intentá de nuevo.",
    },
    "already_subscribed": {
        "pt-BR": "Esta conta já tem uma assinatura ativa.",
        "es-AR": "Esta cuenta ya tiene una suscripción activa.",
    },
    "no_subscription": {
        "pt-BR": "Nenhuma assinatura ativa nesta conta.",
        "es-AR": "Ninguna suscripción activa en esta cuenta.",
    },
    "session_required": {
        "pt-BR": "Faça login para continuar.",
        "es-AR": "Iniciá sesión para continuar.",
    },
}


def message(key: str, locale: str) -> str:
    table = MESSAGES[key]
    return table.get(locale, table["pt-BR"])


class TrialBody(BaseModel):
    """O corpo aceita o formato do Payment Brick como ele vem.

    O Brick do Mercado Pago entrega ``formData`` com a chave ``token``, e a
    landing repassa isso inteiro. A API de preapproval chama o mesmo dado de
    ``card_token_id``. Aceitar os dois nomes evita uma tradução no navegador —
    onde ela sumiria em silêncio se o Brick mudasse o shape.
    """

    model_config = {"extra": "ignore"}

    email: EmailStr
    name: str = Field(default="", max_length=160)
    locale: str = Field(default="es-AR", max_length=10)
    # Token do cartão gerado pelo SDK do Mercado Pago no navegador. O número do
    # cartão nunca chega aqui — é a razão de existir do token.
    card_token_id: str = Field(default="", max_length=120)
    token: str = Field(default="", max_length=120)

    @property
    def card_token(self) -> str:
        return (self.card_token_id or self.token).strip()


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


def sync_entitlements(db: Session, subscription: Subscription) -> None:
    """Espelha o prazo da assinatura nos entitlements do plano.

    Concede o que o Plano Lua libera e carimba ``expires_at`` com o fim do
    período atual. Cancelamento não apaga nada: a pessoa pagou (ou ganhou) até
    ali, e o acesso simplesmente vence sozinho na data.
    """
    expires_at = _aware(subscription.current_period_end)
    for product_id in pricing.granted_products(subscription.product_id):
        entitlement = db.scalar(
            select(Entitlement).where(
                Entitlement.user_id == subscription.user_id,
                Entitlement.product_id == product_id,
            )
        )
        if entitlement:
            entitlement.status = "available"
            entitlement.expires_at = expires_at
            continue
        db.add(
            Entitlement(
                user_id=subscription.user_id,
                product_id=product_id,
                status="available",
                source="site",
                expires_at=expires_at,
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
    locale = pricing.normalize_locale(body.locale)
    market = pricing.market_for(locale)

    # A recorrência existe só onde existe conta recorrente. Ver o docstring.
    if market != "AR":
        raise HTTPException(status_code=409, detail=message("trial_unavailable", locale))
    if not mp.is_enabled():
        raise HTTPException(status_code=503, detail=message("provider_off", locale))
    if not body.card_token:
        raise HTTPException(status_code=400, detail=message("card_required", locale))

    email = body.email.lower()
    user = db.scalar(select(User).where(User.email == email))
    if user and active_subscription(db, user.id):
        raise HTTPException(status_code=409, detail=message("already_subscribed", locale))

    temp_password = ""
    if not user:
        temp_password = secrets.token_urlsafe(9)
        user = User(email=email, password_hash=hash_password(temp_password), name=body.name, locale=locale)
        db.add(user)
        db.flush()

    amount_minor = pricing.amount_minor(PRODUCT_ID, locale)
    currency = pricing.currency_for(locale)
    subscription = Subscription(
        user_id=user.id,
        provider="mercadopago",
        external_id="",
        product_id=PRODUCT_ID,
        status="pending",
        amount_minor=amount_minor,
        currency=currency,
        locale=locale,
        market=market,
    )
    db.add(subscription)
    db.flush()

    try:
        preapproval = mp.create_preapproval(
            amount=amount_minor / 100,
            currency=currency,
            reason=pricing.title_for(PRODUCT_ID, locale),
            payer_email=email,
            card_token_id=body.card_token,
            external_reference=subscription.id,
            trial_days=TRIAL_DAYS,
            back_url=portal_url(),
            notification_url=f"{site_url()}/api/webhooks/mercadopago/ar/subscription",
        )
    except mp.MercadoPagoError as exc:
        # A assinatura não chegou a existir no provedor: desfazemos a nossa para
        # não deixar linha órfã que o webhook nunca vai encontrar.
        logger.warning("Assinatura recusada pelo provedor: %s", exc)
        db.rollback()
        raise HTTPException(status_code=402, detail=message("provider_refused", locale))

    trial_ends_at = _now() + timedelta(days=TRIAL_DAYS)
    subscription.external_id = str(preapproval.get("id") or "")
    subscription.status = "trialing"
    subscription.trial_ends_at = trial_ends_at
    subscription.current_period_end = trial_ends_at
    subscription.raw_payload = _digest(preapproval)
    sync_entitlements(db, subscription)
    db.commit()

    send_purchase_confirmation(
        email=user.email,
        name=user.name,
        product_title=pricing.title_for(PRODUCT_ID, locale),
        amount_label=pricing.format_amount(amount_minor, currency),
        locale=locale,
        temp_password=temp_password or None,
    )

    return {
        "status": "trialing",
        "trial_ends_at": trial_ends_at.isoformat(),
        "trial_days": TRIAL_DAYS,
        "portal_url": portal_url(),
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

    if subscription.external_id:
        try:
            mp.cancel_preapproval(subscription.external_id)
        except mp.MercadoPagoError as exc:
            # Cancelar é a promessa que a landing faz. Se o provedor está fora do
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

    # Idempotência: o Mercado Pago reenvia a mesma notificação até receber 200,
    # e uma renovação contada duas vezes daria dois meses de acesso por um.
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
        novo = PREAPPROVAL_STATUS.get(str(preapproval.get("status") or ""), subscription.status)
        # ``authorized`` durante o período grátis continua sendo trial: quem
        # separa os dois é a data, não o provedor.
        trial_ends_at = _aware(subscription.trial_ends_at)
        if novo == "active" and trial_ends_at and trial_ends_at > _now():
            novo = "trialing"
        subscription.status = novo
        if novo == "cancelled" and not subscription.cancelled_at:
            subscription.cancelled_at = _now()
        subscription.raw_payload = _digest(preapproval)
        sync_entitlements(db, subscription)
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
        # Um mês a partir do fim do período atual, não de hoje: senão cada
        # renovação encurtaria o acesso pelo tempo que a notificação demorou.
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
