"""Lembretes de renovação/trial e recuperação pós-vencimento do Diário Astral.

Sem agendador in-process: morre no redeploy. O cron externo (Coolify) chama
POST /api/tasks/renewal-reminders com o secret em x-task-secret.

Dois fluxos, todos idempotentes:

run_renewal_reminders — entitlements pagos (source != "trial"):
  7d      — 7 dias antes do vencimento
  today   — no dia do vencimento
  winback — 2-4 dias após vencer, só para quem NÃO renovou

run_trial_reminders — entitlements de trial (source == "trial"):
  trial_ending — 1 dia antes do fim do trial (convite para assinar)
  trial_winback — 2-4 dias após o trial vencer sem conversão

Marca em `site_renewal_reminders` por (entitlement_id, reminder_type, expiry_date):
a chave inclui a data de vencimento para que uma segunda expiração (após nova
compra) gere um novo registro, em vez de ser bloqueada pelo primeiro.

O e-mail de winback só sai se GG_CHECKOUT_URLS tiver a URL de
`site:diario_astral_oferta_saida`; sem ela loga e não manda link quebrado.
"""

from __future__ import annotations

import hmac
import logging
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import String, DateTime, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .checkout import gg_checkout_url, portal_url
from .db import Base, get_db
from .mailer import send_renewal_reminder_email, send_trial_ending_email, send_weekly_forecast_email, send_winback_email
from .models import Entitlement, Profile, User

logger = logging.getLogger(__name__)
router = APIRouter()

PRODUCT_ID = "site:diario_astral"
WINBACK_PRODUCT_ID = "site:diario_astral_oferta_saida"


class RenewalReminder(Base):
    """Marca idempotente de lembrete enviado.

    Chave natural: (entitlement_id, reminder_type, expiry_date).
    expiry_date é YYYY-MM-DD UTC do expires_at no momento da varredura — inclui
    a data para que um segundo vencimento (após renovação) gere novo registro.
    """

    __tablename__ = "site_renewal_reminders"
    __table_args__ = (
        UniqueConstraint("entitlement_id", "reminder_type", "expiry_date", name="uq_renewal_reminder"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    entitlement_id: Mapped[str] = mapped_column(String(36), index=True)
    reminder_type: Mapped[str] = mapped_column(String(20))
    expiry_date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD UTC
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _expiry_date_key(expires_at: datetime) -> str:
    return _aware(expires_at).strftime("%Y-%m-%d")


def _already_sent(db: Session, entitlement_id: str, reminder_type: str, expiry_date: str) -> bool:
    return bool(db.scalar(
        select(RenewalReminder).where(
            RenewalReminder.entitlement_id == entitlement_id,
            RenewalReminder.reminder_type == reminder_type,
            RenewalReminder.expiry_date == expiry_date,
        )
    ))


def _mark_sent(db: Session, entitlement_id: str, reminder_type: str, expiry_date: str) -> None:
    db.add(RenewalReminder(
        entitlement_id=entitlement_id,
        reminder_type=reminder_type,
        expiry_date=expiry_date,
        sent_at=_now(),
    ))
    db.commit()


def _renewal_url() -> str | None:
    return gg_checkout_url(PRODUCT_ID)


def _winback_url() -> str | None:
    return gg_checkout_url(WINBACK_PRODUCT_ID)


def run_trial_reminders(db: Session) -> dict:
    """Varre entitlements de trial e dispara lembretes. Seguro p/ chamar N vezes.

    Separa trial de pago pelo ``source == "trial"``: assim run_renewal_reminders
    não manda e-mail de renovação paga para quem ainda está no trial.
    """
    now = _now()
    stats: dict[str, int] = {"trial_ending": 0, "trial_winback": 0, "skipped": 0, "errors": 0}

    subscribe_link = _renewal_url()
    winback_link = _winback_url()

    candidates = db.scalars(
        select(Entitlement).where(
            Entitlement.product_id == PRODUCT_ID,
            Entitlement.source == "trial",
            Entitlement.expires_at.isnot(None),
        )
    ).all()

    for ent in candidates:
        user = db.get(User, ent.user_id)
        if not user:
            continue

        expires_at = _aware(ent.expires_at)
        expiry_key = _expiry_date_key(expires_at)
        delta = expires_at - now

        if timedelta(hours=0) <= delta <= timedelta(hours=36):
            # Trial acaba nas próximas 36h: convite para assinar
            if _already_sent(db, ent.id, "trial_ending", expiry_key):
                stats["skipped"] += 1
                continue
            if not subscribe_link:
                stats["skipped"] += 1
                continue
            locale = getattr(user, "locale", "pt-BR") or "pt-BR"
            result = send_trial_ending_email(
                email=user.email,
                name=user.name or user.email,
                trial_ends_at=expires_at,
                subscribe_url=subscribe_link,
                locale=locale,
            )
            if result.get("sent"):
                _mark_sent(db, ent.id, "trial_ending", expiry_key)
                stats["trial_ending"] += 1
            else:
                logger.warning("Falha ao enviar trial_ending para %s: %s", user.email, result.get("error"))
                stats["errors"] += 1

        elif timedelta(days=-4) <= delta < timedelta(days=-1) and expires_at < now:
            # Trial vencido há 1-4 dias sem conversão
            if winback_link:
                if _already_sent(db, ent.id, "trial_winback", expiry_key):
                    stats["skipped"] += 1
                    continue
                result = send_winback_email(
                    email=user.email,
                    name=user.name or user.email,
                    winback_url=winback_link,
                )
                if result.get("sent"):
                    _mark_sent(db, ent.id, "trial_winback", expiry_key)
                    stats["trial_winback"] += 1
                else:
                    logger.warning("Falha ao enviar trial_winback para %s: %s", user.email, result.get("error"))
                    stats["errors"] += 1
            else:
                stats["skipped"] += 1
        else:
            stats["skipped"] += 1

    return stats


def run_renewal_reminders(db: Session) -> dict:
    """Varre entitlements pagos e dispara os e-mails cabíveis. Seguro p/ chamar N vezes.

    Ignora entitlements de trial (source == "trial") — esses são tratados por
    run_trial_reminders para que os textos e a lógica de conversão sejam corretos.
    """
    now = _now()
    stats: dict[str, int] = {"7d": 0, "1d": 0, "today": 0, "winback": 0, "skipped": 0, "errors": 0}

    candidates = db.scalars(
        select(Entitlement).where(
            Entitlement.product_id == PRODUCT_ID,
            Entitlement.expires_at.isnot(None),
            Entitlement.source != "trial",
        )
    ).all()

    renewal_link = _renewal_url()
    winback_link = _winback_url()

    if not winback_link:
        logger.info(
            "GG_CHECKOUT_URLS sem entrada para %s — e-mails winback não serão enviados. "
            "Configure GG_CHECKOUT_URLS quando o checkout estiver disponível.",
            WINBACK_PRODUCT_ID,
        )

    for ent in candidates:
        user = db.get(User, ent.user_id)
        if not user:
            continue

        expires_at = _aware(ent.expires_at)
        expiry_key = _expiry_date_key(expires_at)
        delta = expires_at - now

        if timedelta(days=6) <= delta <= timedelta(days=8):
            _process_reminder(db, ent, user, "7d", expiry_key, expires_at, renewal_link, stats)

        elif timedelta(hours=12) < delta <= timedelta(hours=36):
            _process_reminder(db, ent, user, "1d", expiry_key, expires_at, renewal_link, stats)

        elif timedelta(hours=-12) <= delta <= timedelta(hours=12):
            _process_reminder(db, ent, user, "today", expiry_key, expires_at, renewal_link, stats)

        elif timedelta(days=-4) <= delta < timedelta(days=-1) and expires_at < now:
            # Vencido há 1-4 dias e ainda não renovado (expires_at segue no passado)
            if winback_link:
                _process_winback(db, ent, user, expiry_key, winback_link, stats)
            else:
                stats["skipped"] += 1

        else:
            stats["skipped"] += 1

    return stats


def _process_reminder(
    db: Session,
    ent: Entitlement,
    user: User,
    reminder_type: str,
    expiry_key: str,
    expires_at: datetime,
    renewal_link: str | None,
    stats: dict,
) -> None:
    if _already_sent(db, ent.id, reminder_type, expiry_key):
        stats["skipped"] += 1
        return

    if not renewal_link:
        logger.warning(
            "GG_CHECKOUT_URLS sem entrada para %s — lembrete %s não enviado para %s. "
            "Configure GG_CHECKOUT_URLS para ativar lembretes.",
            PRODUCT_ID,
            reminder_type,
            user.email,
        )
        stats["skipped"] += 1
        return

    result = send_renewal_reminder_email(
        email=user.email,
        name=user.name or user.email,
        expires_at=expires_at,
        renewal_url=renewal_link,
        reminder_type=reminder_type,
    )
    if result.get("sent"):
        _mark_sent(db, ent.id, reminder_type, expiry_key)
        stats[reminder_type] += 1
    else:
        logger.warning(
            "Falha ao enviar lembrete %s para %s: %s",
            reminder_type,
            user.email,
            result.get("error"),
        )
        stats["errors"] += 1


def _process_winback(
    db: Session,
    ent: Entitlement,
    user: User,
    expiry_key: str,
    winback_link: str,
    stats: dict,
) -> None:
    if _already_sent(db, ent.id, "winback", expiry_key):
        stats["skipped"] += 1
        return

    result = send_winback_email(
        email=user.email,
        name=user.name or user.email,
        winback_url=winback_link,
    )
    if result.get("sent"):
        _mark_sent(db, ent.id, "winback", expiry_key)
        stats["winback"] += 1
    else:
        logger.warning(
            "Falha ao enviar winback para %s: %s",
            user.email,
            result.get("error"),
        )
        stats["errors"] += 1


@router.post("/api/tasks/renewal-reminders")
async def renewal_reminders_task(request: Request, db: Session = Depends(get_db)) -> dict:
    """Endpoint chamado pelo cron do Coolify para disparar lembretes.

    Autenticado por x-task-secret em tempo constante (hmac.compare_digest).
    """
    secret = os.getenv("TASK_SECRET", "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="TASK_SECRET não configurado.")
    provided = request.headers.get("x-task-secret", "")
    if not hmac.compare_digest(secret.encode(), provided.encode()):
        raise HTTPException(status_code=401, detail="Segredo inválido.")

    stats = run_renewal_reminders(db)
    trial_stats = run_trial_reminders(db)
    return {
        "ok": True,
        **stats,
        "trial_ending": trial_stats["trial_ending"],
        "trial_winback": trial_stats["trial_winback"],
        "coupon_10": trial_stats.get("coupon_10", 0) + stats.get("coupon_10", 0),
        "coupon_15": trial_stats.get("coupon_15", 0) + stats.get("coupon_15", 0),
    }


WEEKLY_FORECAST_PRODUCT = "site:mapa_astral"
WEEKLY_FORECAST_CONTENT = "site:content:previsao_semanal"


def run_weekly_forecast(db: Session) -> dict:
    """Gera e envia a previsão semanal para assinantes ativas com mapa_astral.

    Chamado pelo cron do Coolify todo sábado via POST /api/tasks/weekly-forecast.
    Idempotente: a chave (user_id, iso_week) evita reenvio na mesma semana.
    Inclui o texto completo da previsão no e-mail — não só aviso.
    """
    from .engine import generate_reading
    from datetime import date

    now = _now()
    # ISO week de referência (YYYY-Www) — chave de idempotência por semana
    iso_week = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
    stats: dict[str, int] = {"sent": 0, "skipped": 0, "no_profile": 0, "errors": 0}
    portal = portal_url()

    candidates = db.scalars(
        select(Entitlement).where(
            Entitlement.product_id == WEEKLY_FORECAST_PRODUCT,
            Entitlement.status == "available",
        )
    ).all()

    for ent in candidates:
        # Entitlement expirado → pula
        if ent.expires_at is not None and _aware(ent.expires_at) < now:
            stats["skipped"] += 1
            continue

        user = db.get(User, ent.user_id)
        if not user:
            stats["skipped"] += 1
            continue

        # Idempotência: (user_id, "weekly_forecast", iso_week)
        if _already_sent(db, ent.id, "weekly_forecast", iso_week):
            stats["skipped"] += 1
            continue

        profile = db.get(Profile, user.id)
        if not profile or not profile.birth_date or not profile.birth_city:
            stats["no_profile"] += 1
            continue

        locale = getattr(user, "locale", "pt-BR") or "pt-BR"
        try:
            result = generate_reading(
                WEEKLY_FORECAST_CONTENT,
                "Previsão da semana",
                profile,
                locale=locale,
                customer_name=user.name or user.email,
            )
            forecast_html = result.body_html or ""
        except Exception as exc:
            logger.error("Falha ao gerar previsão semanal para %s: %s", user.email, exc)
            stats["errors"] += 1
            continue

        delivery = send_weekly_forecast_email(
            email=user.email,
            name=user.name or user.email,
            forecast_html=forecast_html,
            portal_url=portal,
            locale=locale,
        )
        if delivery.get("sent"):
            _mark_sent(db, ent.id, "weekly_forecast", iso_week)
            stats["sent"] += 1
        else:
            logger.error(
                "Falha ao enviar previsão semanal para %s: %s",
                user.email,
                delivery.get("error"),
            )
            stats["errors"] += 1

    return stats


@router.post("/api/tasks/weekly-forecast")
async def weekly_forecast_task(request: Request, db: Session = Depends(get_db)) -> dict:
    """Cron do Coolify chama todo sábado para disparar a previsão semanal.

    Mesmo mecanismo de autenticação do /api/tasks/renewal-reminders.
    Configurar no Coolify: cron sábado + header x-task-secret.
    """
    secret = os.getenv("TASK_SECRET", "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="TASK_SECRET não configurado.")
    provided = request.headers.get("x-task-secret", "")
    if not hmac.compare_digest(secret.encode(), provided.encode()):
        raise HTTPException(status_code=401, detail="Segredo inválido.")

    stats = run_weekly_forecast(db)
    return {"ok": True, **stats}
