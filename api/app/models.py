from datetime import date, datetime, time, timezone
from uuid import uuid4

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "site_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(160), default="")
    locale: Mapped[str] = mapped_column(String(10), default="pt-BR")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    # ``token_epoch`` é um nonce que muda após cada reset de senha. O JWT da
    # sessão inclui o epoch atual; ao resetar, incrementamos o contador e
    # tokens antigos falham na validação (invalidando todas as sessões).
    token_epoch: Mapped[int] = mapped_column(Integer, default=0)
    profile: Mapped["Profile | None"] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")
    entitlements: Mapped[list["Entitlement"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    readings: Mapped[list["Reading"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    reset_tokens: Mapped[list["PasswordResetToken"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class PasswordResetToken(Base):
    __tablename__ = "site_password_reset_tokens"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_site_reset_token_hash"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("site_users.id", ondelete="CASCADE"), index=True)
    # SHA-256 hex do token bruto: nunca guardamos o plaintext. O token de
    # 32 bytes é entregue por e-mail; o link é montado com ele.
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    locale: Mapped[str] = mapped_column(String(10), default="pt-BR")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    user: Mapped[User] = relationship(back_populates="reset_tokens")


class Profile(Base):
    __tablename__ = "site_profiles"

    user_id: Mapped[str] = mapped_column(ForeignKey("site_users.id", ondelete="CASCADE"), primary_key=True)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    birth_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    birth_city: Mapped[str] = mapped_column(String(160), default="")
    birth_country: Mapped[str] = mapped_column(String(2), default="BR")
    birth_timezone: Mapped[str] = mapped_column(String(64), default="America/Sao_Paulo")
    birth_latitude: Mapped[str | None] = mapped_column(String(32), nullable=True)
    birth_longitude: Mapped[str | None] = mapped_column(String(32), nullable=True)
    partner_name: Mapped[str] = mapped_column(String(160), default="")
    partner_birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    partner_birth_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    partner_birth_city: Mapped[str] = mapped_column(String(160), default="")
    partner_country: Mapped[str] = mapped_column(String(2), default="BR")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    user: Mapped[User] = relationship(back_populates="profile")


class Order(Base):
    __tablename__ = "site_orders"
    __table_args__ = (UniqueConstraint("provider", "external_id", name="uq_site_order_provider_external"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("site_users.id", ondelete="CASCADE"), index=True, nullable=True)
    provider: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(255))
    product_id: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(32), default="paid")
    amount_minor: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="BRL")
    locale: Mapped[str] = mapped_column(String(10), default="pt-BR")
    market: Mapped[str] = mapped_column(String(8), default="BR")
    customer_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Entitlement(Base):
    __tablename__ = "site_entitlements"
    __table_args__ = (UniqueConstraint("user_id", "product_id", name="uq_site_entitlement_user_product"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("site_users.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(32), default="available")
    source: Mapped[str] = mapped_column(String(40), default="site")
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user: Mapped[User] = relationship(back_populates="entitlements")


class Subscription(Base):
    """Assinatura recorrente do canal site.

    Separada de ``Order`` de propósito: um pedido é um evento (aconteceu, tem
    valor, acabou), uma assinatura é um estado que vive e muda por meses. Juntar
    os dois faria cada renovação mensal virar uma linha de pedido sem dono claro
    do estado atual.

    ``external_id`` é o preapproval do Mercado Pago. Único por provedor: o
    webhook chega identificando só ele, e é por ele que a gente encontra de quem
    é a assinatura.
    """

    __tablename__ = "site_subscriptions"
    __table_args__ = (UniqueConstraint("provider", "external_id", name="uq_site_subscription_provider_external"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("site_users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(40), default="mercadopago")
    external_id: Mapped[str] = mapped_column(String(255), index=True)
    product_id: Mapped[str] = mapped_column(String(120), default="site:plano_lua")
    # trialing -> active -> cancelled | expired. O vocabulário é nosso; o do
    # provedor entra traduzido, como já acontece com os pagamentos.
    status: Mapped[str] = mapped_column(String(32), default="trialing")
    amount_minor: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="ARS")
    locale: Mapped[str] = mapped_column(String(10), default="es-AR")
    market: Mapped[str] = mapped_column(String(2), default="AR")
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Até quando o acesso está pago. É o valor copiado para
    # ``Entitlement.expires_at`` — o entitlement é quem o portal consulta.
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict)


class Reading(Base):
    __tablename__ = "site_readings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("site_users.id", ondelete="CASCADE"), index=True)
    content_id: Mapped[str] = mapped_column(String(120), index=True)
    product_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    title: Mapped[str] = mapped_column(String(200), default="")
    body_html: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(16), default="llm")
    # Lista de {"title", "subtitle", "order", "content"} quando o content_id é
    # seccionado (ver engine.SECTIONS_BY_CONTENT_ID). ``body_html`` continua
    # sendo preenchido sempre, para compat com leituras antigas e com qualquer
    # consumidor que ainda espere o blob de texto único.
    content_sections: Mapped[list] = mapped_column(JSON, default=list)
    reading_kind: Mapped[str] = mapped_column(String(32), default="love")
    generated_at: Mapped[str] = mapped_column(String(40), default="")
    input_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    user: Mapped[User] = relationship(back_populates="readings")


class WebhookEvent(Base):
    __tablename__ = "site_webhook_events"
    __table_args__ = (UniqueConstraint("provider", "event_id", name="uq_site_webhook_provider_event"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    provider: Mapped[str] = mapped_column(String(40))
    event_id: Mapped[str] = mapped_column(String(255))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
