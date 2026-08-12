"""Web Push com VAPID para o AstroDicas.

Fluxos:
  POST /api/me/push/subscribe      — registra subscription (requer sessão)
  DELETE /api/me/push/unsubscribe  — remove por endpoint
  GET  /api/me/push/vapid-public-key — entrega chave pública (sem auth)

Envio:
  send_push_to_user(db, user_id, title, body, data, locale) — envia para todas
  as subscriptions ativas do usuário, verifica entitlement antes, limpa 410s.

Gatilhos:
  notify_reading_done(reading_id, user_id, locale, title) — chamado pelo worker
  notify_daily_horoscope(db, user_id, locale)             — chamado pelo job diário
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from uuid import uuid4

from fastapi import APIRouter, Cookie, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import String, Text, DateTime, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .db import Base, SessionLocal, get_db
from .entitlements import active as active_entitlement
from .models import now_utc
from .security import decode_token

logger = logging.getLogger(__name__)
router = APIRouter()

PRODUCT_ID = "site:diario_astral"

# Configurados via env em produção. Sem elas o envio é silenciosamente desativado.
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_SUBJECT = os.getenv("VAPID_SUBJECT", "mailto:contato@astrodicas.com.br")


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class PushSubscription(Base):
    __tablename__ = "site_push_subscriptions"
    __table_args__ = (
        UniqueConstraint("user_id", "endpoint_hash", name="uq_push_sub_user_endpoint"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    # Endpoint completo — pode ser longo (FCM URLs têm >200 chars)
    endpoint: Mapped[str] = mapped_column(Text)
    # SHA-256 hex do endpoint para UNIQUE constraint (TEXT não indexa bem)
    endpoint_hash: Mapped[str] = mapped_column(String(64))
    p256dh: Mapped[str] = mapped_column(Text)
    auth: Mapped[str] = mapped_column(Text)
    locale: Mapped[str] = mapped_column(String(10), default="pt-BR")
    created_at: Mapped[str] = mapped_column(DateTime(timezone=True), default=now_utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _endpoint_hash(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode()).hexdigest()


def _vapid_configured() -> bool:
    return bool(VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY)


def _send_one(sub: PushSubscription, payload: dict) -> str | None:
    """Envia push para uma subscription. Retorna 'expired' se 410, None se ok."""
    if not _vapid_configured():
        logger.debug("VAPID não configurado — push ignorado.")
        return None
    try:
        from pywebpush import webpush, WebPushException  # type: ignore
        webpush(
            subscription_info={
                "endpoint": sub.endpoint,
                "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
            },
            data=json.dumps(payload),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": VAPID_SUBJECT},
            content_encoding="aes128gcm",
        )
    except Exception as exc:
        msg = str(exc)
        # 410 Gone: subscription expirada/revogada — limpar
        if "410" in msg or "404" in msg:
            return "expired"
        logger.warning("Falha de push para sub %s: %s", sub.id[:8], msg)
    return None


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------

def send_push_to_user(
    db: Session,
    user_id: str,
    title: str,
    body: str,
    data: dict | None = None,
    locale: str = "pt-BR",
) -> None:
    """Envia push para todas as subscriptions do usuário.

    Verifica entitlement ativo antes de enviar (nunca push para acesso vencido).
    Remove subscriptions com resposta 410.
    """
    ent = active_entitlement(db, user_id, PRODUCT_ID)
    if not ent:
        logger.debug("send_push_to_user: user %s sem entitlement ativo — ignorado.", user_id[:8])
        return

    subs = db.scalars(
        select(PushSubscription).where(PushSubscription.user_id == user_id)
    ).all()
    if not subs:
        return

    payload = {"title": title, "body": body, **(data or {})}
    dead_ids = []
    for sub in subs:
        result = _send_one(sub, payload)
        if result == "expired":
            dead_ids.append(sub.id)

    if dead_ids:
        for sub_id in dead_ids:
            dead = db.get(PushSubscription, sub_id)
            if dead:
                db.delete(dead)
        db.commit()


# ---------------------------------------------------------------------------
# Gatilhos
# ---------------------------------------------------------------------------

def notify_reading_done(reading_id: str, user_id: str, locale: str, reading_title: str) -> None:
    """Notifica o usuário que a leitura ficou pronta. Abre sessão própria."""
    db = SessionLocal()
    try:
        if locale == "es-AR":
            title = "¡Tu lectura está lista!"
            body = f"{reading_title} está disponible. Abrí el portal para leerla."
        else:
            title = "Sua leitura está pronta!"
            body = f"{reading_title} ficou pronta. Abra o portal para ler."
        send_push_to_user(db, user_id, title, body, {"type": "reading_done", "reading_id": reading_id}, locale)
    finally:
        db.close()


def notify_daily_horoscope(db: Session, user_id: str, locale: str) -> None:
    """Notifica que o horóscopo do dia está disponível."""
    if locale == "es-AR":
        title = "Tu horóscopo de hoy está listo"
        body = "Tu lectura diaria está esperándote en el portal."
    else:
        title = "Seu horóscopo de hoje está pronto"
        body = "Sua leitura diária está te esperando no portal."
    send_push_to_user(db, user_id, title, body, {"type": "daily_horoscope"}, locale)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

class SubscribeBody(BaseModel):
    endpoint: str
    p256dh: str
    auth: str
    locale: str = "pt-BR"


@router.get("/api/me/push/vapid-public-key")
def vapid_public_key() -> dict:
    return {"publicKey": VAPID_PUBLIC_KEY}


@router.post("/api/me/push/subscribe", status_code=201)
def subscribe(
    body: SubscribeBody,
    site_session: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> dict:
    payload = decode_token(site_session or "")
    if not payload:
        raise HTTPException(401, "Sessão inválida")
    user_id = payload["user_id"]

    ep_hash = _endpoint_hash(body.endpoint)
    existing = db.scalar(
        select(PushSubscription).where(
            PushSubscription.user_id == user_id,
            PushSubscription.endpoint_hash == ep_hash,
        )
    )
    if existing:
        # Atualiza chaves (podem mudar após re-subscription)
        existing.p256dh = body.p256dh
        existing.auth = body.auth
        existing.locale = body.locale
        db.commit()
        return {"status": "updated"}

    sub = PushSubscription(
        user_id=user_id,
        endpoint=body.endpoint,
        endpoint_hash=ep_hash,
        p256dh=body.p256dh,
        auth=body.auth,
        locale=body.locale,
    )
    db.add(sub)
    db.commit()
    return {"status": "created"}


class UnsubscribeBody(BaseModel):
    endpoint: str


@router.delete("/api/me/push/unsubscribe", status_code=200)
def unsubscribe(
    body: UnsubscribeBody,
    site_session: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> dict:
    payload = decode_token(site_session or "")
    if not payload:
        raise HTTPException(401, "Sessão inválida")
    user_id = payload["user_id"]

    ep_hash = _endpoint_hash(body.endpoint)
    sub = db.scalar(
        select(PushSubscription).where(
            PushSubscription.user_id == user_id,
            PushSubscription.endpoint_hash == ep_hash,
        )
    )
    if sub:
        db.delete(sub)
        db.commit()
    return {"status": "ok"}
