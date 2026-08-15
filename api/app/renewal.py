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
from .mailer import send_astro_alert_email, send_coupon_winback_email, send_renewal_reminder_email, send_trial_ending_email, send_weekly_forecast_email, send_winback_email
from .models import Entitlement, GenerationJob, Profile, Reading, User

logger = logging.getLogger(__name__)
router = APIRouter()

PRODUCT_ID = "site:diario_astral"
WINBACK_PRODUCT_ID = "site:diario_astral_oferta_saida"

# Cupons criados no GG Checkout (IDs: ASTRO10=eRf1ULgblcDrm4a94FEe, ASTRO15=OPqAQ0UHD8bokeoJM1NZ).
# Env vars substituem os defaults em caso de rotação de código.
_GG_COUPON_D1_ENV = "GG_COUPON_D1"
_GG_COUPON_D7_ENV = "GG_COUPON_D7"
_COUPON_D1_DEFAULT = "ASTRO10"
_COUPON_D7_DEFAULT = "ASTRO15"


def _get_coupon_code(env_key: str, default: str) -> str | None:
    """Retorna o código do cupom ou None se ausente — loga error nesse caso."""
    code = os.getenv(env_key, default).strip()
    if not code:
        logger.error(
            "Cupom de recuperação não configurado (%s). "
            "Configure %s=<código> para habilitar e-mails com desconto.",
            env_key,
            env_key,
        )
        return None
    return code


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
    stats: dict[str, int] = {"trial_ending": 0, "trial_winback": 0, "coupon_10": 0, "coupon_15": 0, "skipped": 0, "errors": 0}

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
        locale = getattr(user, "locale", "pt-BR") or "pt-BR"

        if timedelta(hours=0) <= delta <= timedelta(hours=36):
            # Trial acaba nas próximas 36h: convite para assinar
            if _already_sent(db, ent.id, "trial_ending", expiry_key):
                stats["skipped"] += 1
                continue
            if not subscribe_link:
                stats["skipped"] += 1
                continue
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

        elif timedelta(hours=-36) <= delta < timedelta(hours=-12) and expires_at < now:
            # Trial vencido há 12-36h: cupom de recuperação D+1 (10% off)
            _process_coupon_winback(
                db, ent, user, expiry_key, "coupon_10",
                _get_coupon_code(_GG_COUPON_D1_ENV, _COUPON_D1_DEFAULT), 10,
                is_last_chance=False, renewal_link=subscribe_link,
                winback_link=winback_link, locale=locale, stats=stats,
            )

        elif timedelta(days=-4) <= delta < timedelta(hours=-36) and expires_at < now:
            # Trial vencido há 36h-4 dias sem conversão
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

        elif timedelta(days=-8) <= delta < timedelta(days=-6) and expires_at < now:
            # Trial vencido há 6-8 dias: última chance com 15% off
            _process_coupon_winback(
                db, ent, user, expiry_key, "coupon_15",
                _get_coupon_code(_GG_COUPON_D7_ENV, _COUPON_D7_DEFAULT), 15,
                is_last_chance=True, renewal_link=subscribe_link,
                winback_link=winback_link, locale=locale, stats=stats,
            )

        else:
            stats["skipped"] += 1

    return stats


def run_renewal_reminders(db: Session) -> dict:
    """Varre entitlements pagos e dispara os e-mails cabíveis. Seguro p/ chamar N vezes.

    Ignora entitlements de trial (source == "trial") — esses são tratados por
    run_trial_reminders para que os textos e a lógica de conversão sejam corretos.
    """
    now = _now()
    stats: dict[str, int] = {"7d": 0, "1d": 0, "today": 0, "winback": 0, "coupon_10": 0, "coupon_15": 0, "skipped": 0, "errors": 0}

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

        elif timedelta(hours=-36) <= delta < timedelta(hours=-12) and expires_at < now:
            # Vencido há 12-36h (D+1): cupom 10% off
            locale = getattr(user, "locale", "pt-BR") or "pt-BR"
            _process_coupon_winback(
                db, ent, user, expiry_key, "coupon_10",
                _get_coupon_code(_GG_COUPON_D1_ENV, _COUPON_D1_DEFAULT), 10,
                is_last_chance=False, renewal_link=renewal_link,
                winback_link=winback_link, locale=locale, stats=stats,
            )

        elif timedelta(days=-4) <= delta < timedelta(hours=-36) and expires_at < now:
            # Vencido há 36h-4 dias e ainda não renovado
            if winback_link:
                _process_winback(db, ent, user, expiry_key, winback_link, stats)
            else:
                stats["skipped"] += 1

        elif timedelta(days=-8) <= delta < timedelta(days=-6) and expires_at < now:
            # Vencido há 6-8 dias (D+7): última chance 15% off
            locale = getattr(user, "locale", "pt-BR") or "pt-BR"
            _process_coupon_winback(
                db, ent, user, expiry_key, "coupon_15",
                _get_coupon_code(_GG_COUPON_D7_ENV, _COUPON_D7_DEFAULT), 15,
                is_last_chance=True, renewal_link=renewal_link,
                winback_link=winback_link, locale=locale, stats=stats,
            )

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


def _process_coupon_winback(
    db: Session,
    ent: "Entitlement",
    user: "User",
    expiry_key: str,
    reminder_type: str,
    coupon_code: str | None,
    discount_pct: int,
    is_last_chance: bool,
    renewal_link: str | None,
    winback_link: str | None,
    locale: str,
    stats: dict,
) -> None:
    """Envia e-mail de recuperação com cupom de desconto.

    Bloqueia o envio (logger.error) se o código do cupom não estiver configurado.
    Para Argentina (locale es-AR) usa winback_link como fallback sem cupom,
    pois o Mercado Pago não suporta cupons GG Checkout de forma nativa.
    """
    if coupon_code is None:
        # Erro já logado em _get_coupon_code; conta como error p/ visibilidade.
        stats["errors"] += 1
        return

    if _already_sent(db, ent.id, reminder_type, expiry_key):
        stats["skipped"] += 1
        return

    is_ar = locale == "es-AR"
    # Argentina: usa oferta de saída como URL alternativa (preço já reduzido).
    effective_url = (winback_link or renewal_link) if is_ar else renewal_link
    effective_code = "" if is_ar else coupon_code

    if not effective_url:
        logger.warning(
            "URL de checkout ausente para %s — skipping cupom para %s.",
            reminder_type,
            user.email,
        )
        stats["skipped"] += 1
        return

    result = send_coupon_winback_email(
        email=user.email,
        name=user.name or user.email,
        checkout_url=effective_url,
        coupon_code=effective_code,
        discount_pct=discount_pct,
        is_last_chance=is_last_chance,
        locale=locale,
    )
    if result.get("sent"):
        _mark_sent(db, ent.id, reminder_type, expiry_key)
        stats[reminder_type] = stats.get(reminder_type, 0) + 1
    else:
        logger.warning(
            "Falha ao enviar %s para %s: %s",
            reminder_type,
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


# Previsão semanal pertence ao Diário Astral pago (source != "trial").
# Era "mapa_astral" como atalho histórico; quem compra o mapa avulso não
# deve receber a previsão semanal — ela é do plano, não do produto avulso.
WEEKLY_FORECAST_PRODUCT = "site:diario_astral"
WEEKLY_FORECAST_CONTENT = "site:content:previsao_semanal"


def run_weekly_forecast(db: Session) -> dict:
    """Gera e envia a previsão semanal para assinantes pagas do Diário Astral.

    Chamado pelo cron do Coolify todo sábado via POST /api/tasks/weekly-forecast.
    Idempotente: a chave (entitlement_id, iso_week) evita reenvio na mesma semana.
    Inclui o texto completo da previsão no e-mail — não só aviso.
    """
    from .engine import generate_reading

    now = _now()
    # ISO week de referência (YYYY-Www) — chave de idempotência por semana
    iso_week = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
    stats: dict[str, int] = {"sent": 0, "skipped": 0, "no_profile": 0, "errors": 0}
    portal = portal_url()

    candidates = db.scalars(
        select(Entitlement).where(
            Entitlement.product_id == WEEKLY_FORECAST_PRODUCT,
            Entitlement.status == "available",
            # Trial não recebe previsão semanal — só acesso pago.
            Entitlement.source != "trial",
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


# ---------------------------------------------------------------------------
# Avisos de lua nova, lua cheia e retrogradações
# ---------------------------------------------------------------------------

class AstroAlertSent(Base):
    """Idempotência de aviso astral: (user_id, event_type, event_date) único.

    Evita reenvio do mesmo alerta no mesmo dia, mesmo que o cron dispare
    duas vezes (falha + retry, ou deploy duplo).
    """

    __tablename__ = "site_astro_alerts_sent"
    __table_args__ = (
        UniqueConstraint("user_id", "event_type", "event_date", name="uq_astro_alert"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(40))   # lua_nova | lua_cheia | retrogrado:Mercúrio
    event_date: Mapped[str] = mapped_column(String(10))   # YYYY-MM-DD UTC
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def _alert_already_sent(db: Session, user_id: str, event_type: str, event_date: str) -> bool:
    return bool(db.scalar(
        select(AstroAlertSent).where(
            AstroAlertSent.user_id == user_id,
            AstroAlertSent.event_type == event_type,
            AstroAlertSent.event_date == event_date,
        )
    ))


def _mark_alert_sent(db: Session, user_id: str, event_type: str, event_date: str) -> None:
    db.add(AstroAlertSent(user_id=user_id, event_type=event_type, event_date=event_date, sent_at=_now()))
    db.commit()


def run_astro_alerts(db: Session) -> dict:
    """Detecta eventos astronômicos de hoje e envia e-mail para assinantes pagas.

    Chamado pelo cron diário via POST /api/tasks/astro-alerts.
    Idempotente por (user_id, event_type, event_date): o mesmo alerta não
    sai duas vezes para o mesmo usuário no mesmo dia.
    Só para quem tem acesso pago (source != "trial") ao Diário Astral.
    """
    from .astrology import lunar_event_today, retrograde_starts_today

    now = _now()
    today_str = now.strftime("%Y-%m-%d")
    today_date = now.date()

    # Detecta eventos do dia
    events: list[tuple[str, str | None]] = []  # (event_type, planet_or_None)
    lunar = lunar_event_today(today_date)
    if lunar:
        events.append((lunar, None))
    for planet in retrograde_starts_today(today_date):
        events.append((f"retrogrado:{planet}", planet))

    if not events:
        return {"sent": 0, "skipped": 0, "errors": 0, "events": []}

    event_labels = [e[0] for e in events]
    stats: dict[str, int] = {"sent": 0, "skipped": 0, "errors": 0}
    portal = portal_url()

    # Assinantes com acesso pago ativo ao Diário Astral
    candidates = db.scalars(
        select(Entitlement).where(
            Entitlement.product_id == PRODUCT_ID,
            Entitlement.status == "available",
            Entitlement.source != "trial",
        )
    ).all()

    for ent in candidates:
        if ent.expires_at is not None and _aware(ent.expires_at) < now:
            stats["skipped"] += 1
            continue

        user = db.get(User, ent.user_id)
        if not user:
            stats["skipped"] += 1
            continue

        locale = getattr(user, "locale", "pt-BR") or "pt-BR"

        for event_type, planet in events:
            if _alert_already_sent(db, user.id, event_type, today_str):
                stats["skipped"] += 1
                continue

            mail_event = event_type.split(":")[0] if event_type.startswith("retrogrado:") else event_type
            delivery = send_astro_alert_email(
                email=user.email,
                name=user.name or user.email,
                event_type=mail_event,
                planet=planet,
                portal_url=portal,
                locale=locale,
            )
            if delivery.get("sent"):
                _mark_alert_sent(db, user.id, event_type, today_str)
                stats["sent"] += 1
            else:
                logger.error(
                    "Falha ao enviar alerta %s para %s: %s",
                    event_type, user.email, delivery.get("error"),
                )
                stats["errors"] += 1

    return {**stats, "events": event_labels}


@router.post("/api/tasks/astro-alerts")
async def astro_alerts_task(request: Request, db: Session = Depends(get_db)) -> dict:
    """Cron diário para disparar avisos de lua e retrógrados.

    Mesmo mecanismo de autenticação dos outros tasks.
    Configurar no Coolify: cron diário (ex.: 07:00 UTC) + header x-task-secret.
    """
    secret = os.getenv("TASK_SECRET", "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="TASK_SECRET não configurado.")
    provided = request.headers.get("x-task-secret", "")
    if not hmac.compare_digest(secret.encode(), provided.encode()):
        raise HTTPException(status_code=401, detail="Segredo inválido.")

    stats = run_astro_alerts(db)
    return {"ok": True, **stats}


@router.post("/api/tasks/push-daily-horoscope")
async def push_daily_horoscope_task(request: Request, db: Session = Depends(get_db)) -> dict:
    """Cron diário para notificar usuários com push que o horóscopo está pronto.

    Enviar depois que o horóscopo do dia já estiver gerado (ex.: 07:30 UTC).
    Autentica por x-task-secret, mesmo padrão dos outros tasks.
    """
    secret = os.getenv("TASK_SECRET", "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="TASK_SECRET não configurado.")
    provided = request.headers.get("x-task-secret", "")
    if not hmac.compare_digest(secret.encode(), provided.encode()):
        raise HTTPException(status_code=401, detail="Segredo inválido.")

    from .push_notifications import notify_daily_horoscope, PushSubscription  # late import
    from .models import Entitlement
    from sqlalchemy import or_

    now = _now()
    # Coleta todos os users com push subscriptions e entitlement ativo
    subs = db.scalars(select(PushSubscription)).all()
    seen_users: set[str] = set()
    sent = 0
    skipped = 0
    for sub in subs:
        if sub.user_id in seen_users:
            continue
        seen_users.add(sub.user_id)
        ent = db.scalar(
            select(Entitlement).where(
                Entitlement.user_id == sub.user_id,
                Entitlement.product_id == PRODUCT_ID,
                Entitlement.status == "available",
                or_(Entitlement.expires_at.is_(None), Entitlement.expires_at >= now),
            )
        )
        if not ent:
            skipped += 1
            continue
        try:
            notify_daily_horoscope(db, sub.user_id, sub.locale)
            sent += 1
        except Exception:
            logger.exception("Falha ao enviar push diário para user %s", sub.user_id[:8])

    return {"ok": True, "sent": sent, "skipped": skipped}


# ---------------------------------------------------------------------------
# Pré-geração diária de conteúdo para todas as clientes ativas
# ---------------------------------------------------------------------------

_PREGEN_PROFILE_KEYS = (
    "user_id", "birth_date", "birth_time", "birth_city", "birth_state",
    "birth_country", "birth_timezone", "birth_latitude", "birth_longitude",
    "partner_name", "partner_birth_date", "partner_birth_time",
    "partner_birth_city", "partner_birth_state", "partner_country",
)


def _profile_to_snapshot(profile: Profile | None) -> dict | None:
    """Réplica local de main.profile_to_dict — evita import circular."""
    from datetime import date, time
    if not profile:
        return None
    return {
        key: getattr(profile, key).isoformat()
        if isinstance(getattr(profile, key), (date, time))
        else getattr(profile, key)
        for key in _PREGEN_PROFILE_KEYS
    }


def _pregen_target_iso_week(locale: str) -> str:
    """Réplica local de main._target_iso_week — evita import circular."""
    from . import horoscope_free
    today = horoscope_free.local_today(locale if locale in horoscope_free.LOCALE_DEFAULTS else "pt-BR")
    if today.weekday() >= 5:
        next_monday = today + timedelta(days=7 - today.weekday())
        year, week, _ = next_monday.isocalendar()
    else:
        year, week, _ = today.isocalendar()
    return f"{year}-W{week:02d}"


def _pregen_snapshot_for(content_id: str, profile: Profile, locale: str) -> dict | None:
    base = _profile_to_snapshot(profile)
    if content_id == "site:content:previsao_semanal" and base is not None:
        base = {**base, "target_iso_week": _pregen_target_iso_week(locale)}
    return base

# Conteúdos do plano de 30 dias com sua cadência de pré-geração.
# "daily"   → gera toda vez que o job rodar (idempotência: ready hoje)
# "weekly"  → gera na semana ISO corrente (idempotência: ready na semana)
# "monthly" → gera no mês corrente (idempotência: ready no mês)
_PREGEN_SCHEDULE: dict[str, str] = {
    "site:content:horoscopo_diario": "daily",
    "site:content:previsao_semanal": "weekly",
    "site:content:guia_do_mes": "monthly",
}

# Título exibido na leitura — espelha content_title() em main.py para não
# importar main aqui (importação circular). Mantido em sync manual.
_PREGEN_TITLES: dict[str, str] = {
    "site:content:horoscopo_diario": "Horóscopo do Dia",
    "site:content:previsao_semanal": "Previsão da Semana",
    "site:content:guia_do_mes": "Guia do Mês",
}


def _pregen_period_key(content_id: str, now: datetime) -> str:
    """Chave de idempotência por período: YYYY-MM-DD, YYYY-Www ou YYYY-MM."""
    cadence = _PREGEN_SCHEDULE.get(content_id, "daily")
    if cadence == "monthly":
        return now.strftime("%Y-%m")
    if cadence == "weekly":
        iso = now.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    return now.strftime("%Y-%m-%d")


def _reading_fresh_for_period(db: Session, user_id: str, content_id: str, period_key: str) -> bool:
    """True quando já existe leitura pronta OU em andamento para o período.

    'em andamento' (in_progress) basta para não duplicar: o worker vai concluir.
    """
    cadence = _PREGEN_SCHEDULE.get(content_id, "daily")
    now = _now()

    if cadence == "daily":
        # Pronto hoje UTC
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return bool(db.scalar(
            select(Reading).where(
                Reading.user_id == user_id,
                Reading.content_id == content_id,
                Reading.status.in_(["ready", "in_progress"]),
                Reading.created_at >= day_start,
            )
        ))

    if cadence == "weekly":
        # Pronto na mesma semana ISO
        iso_year, iso_week, _ = now.isocalendar()
        week_start = now - timedelta(days=now.weekday())
        week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
        week_end = week_start + timedelta(days=7)
        return bool(db.scalar(
            select(Reading).where(
                Reading.user_id == user_id,
                Reading.content_id == content_id,
                Reading.status.in_(["ready", "in_progress"]),
                Reading.created_at >= week_start,
                Reading.created_at < week_end,
            )
        ))

    # monthly
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if now.month == 12:
        month_end = now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        month_end = now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return bool(db.scalar(
        select(Reading).where(
            Reading.user_id == user_id,
            Reading.content_id == content_id,
            Reading.status.in_(["ready", "in_progress"]),
            Reading.created_at >= month_start,
            Reading.created_at < month_end,
        )
    ))


def _job_active(db: Session, user_id: str, content_id: str) -> bool:
    return bool(db.scalar(
        select(GenerationJob).where(
            GenerationJob.user_id == user_id,
            GenerationJob.content_id == content_id,
            GenerationJob.status.in_(["queued", "running"]),
        )
    ))


def run_daily_pregen(db: Session) -> dict:
    """Enfileira geração de conteúdo do plano de 30 dias para todas as clientes ativas.

    Chamado diariamente via POST /api/tasks/daily-pregen. Idempotente: não
    duplica job nem leitura já pronta para o período corrente. Distribui a
    carga no tempo via not_before escalonado (PREGEN_STAGGER_SECONDS, padrão 3s
    entre enfileiramentos) para não inundar o pool de workers nem a API do MiniMax.

    Gera para trial E pagante — a dona decidiu: a leitura do dia pertence à
    cliente independente de ela abrir o app (e sem conteúdo não há push/email).
    """
    from .worker import enqueue_generation_job
    from .engine import sections_for

    now = _now()
    stagger = int(os.getenv("PREGEN_STAGGER_SECONDS", "3"))
    stats: dict[str, int] = {"enqueued": 0, "skipped_ready": 0, "skipped_job": 0, "skipped_no_profile": 0, "errors": 0}
    order = 0  # contador para escalonamento

    # Todos os entitlements ativos do produto base (trial + pago)
    candidates = db.scalars(
        select(Entitlement).where(
            Entitlement.product_id == PRODUCT_ID,
            Entitlement.status == "available",
        )
    ).all()

    for ent in candidates:
        # Entitlement expirado → pula
        if ent.expires_at is not None and _aware(ent.expires_at) < now:
            stats["skipped_ready"] += 1
            continue

        user = db.get(User, ent.user_id)
        if not user:
            continue

        profile = db.get(Profile, user.id)
        if not profile or not profile.birth_date or not profile.birth_city:
            # Não basta contar. Estas pessoas TÊM acesso liberado e não recebem
            # leitura nenhuma — sem data e cidade de nascimento não há mapa para
            # calcular. Antes o `continue` era mudo: 10 clientes sumiam todo dia
            # num contador, sem log e sem tela no admin, então ninguém tinha como
            # agir. Quem pagou conclui que o produto não funciona.
            stats["skipped_no_profile"] += 1
            stats.setdefault("sem_perfil", []).append(
                {"user_id": user.id, "email": user.email, "product_id": ent.product_id}
            )
            logger.warning(
                "pregen_sem_perfil user=%s email=%s product=%s — acesso liberado sem "
                "dados de nascimento; cliente não recebe leitura",
                user.id, user.email, ent.product_id,
            )
            continue

        locale = getattr(user, "locale", "pt-BR") or "pt-BR"

        for content_id, cadence in _PREGEN_SCHEDULE.items():
            period_key = _pregen_period_key(content_id, now)

            if _reading_fresh_for_period(db, user.id, content_id, period_key):
                stats["skipped_ready"] += 1
                continue

            if _job_active(db, user.id, content_id):
                stats["skipped_job"] += 1
                continue

            try:
                title = _PREGEN_TITLES.get(content_id, content_id)
                expected_sections = sections_for(content_id, profile, locale)
                snapshot = _pregen_snapshot_for(content_id, profile, locale)
                reading = Reading(
                    user_id=user.id,
                    content_id=content_id,
                    product_id=ent.product_id,
                    status="in_progress",
                    title=title,
                    source="llm",
                    input_snapshot=snapshot,
                    sections_total=len(expected_sections),
                    sections_done=0,
                )
                db.add(reading)
                db.commit()
                db.refresh(reading)

                not_before = now + timedelta(seconds=order * stagger)
                job = enqueue_generation_job(
                    reading_id=reading.id,
                    content_id=content_id,
                    user_id=user.id,
                    locale=locale,
                    customer_name=user.name or "",
                )
                if job and not_before > now:
                    # Sobrescreve not_before para escalonamento
                    from .db import SessionLocal as _SL
                    with _SL() as _db:
                        _job = _db.get(GenerationJob, job.id)
                        if _job and _job.status == "queued":
                            _job.not_before = not_before
                            _db.commit()

                order += 1
                stats["enqueued"] += 1
                logger.info(
                    "pregen_enqueued user=%s content_id=%s not_before=%s",
                    user.id[:8], content_id, not_before.isoformat(),
                )
            except Exception:
                logger.exception("Falha ao enfileirar pregen para user=%s content_id=%s", user.id[:8], content_id)
                stats["errors"] += 1

    return stats


@router.post("/api/tasks/daily-pregen")
async def daily_pregen_task(request: Request, db: Session = Depends(get_db)) -> dict:
    """Cron diário para pré-gerar conteúdo do plano de 30 dias para todas as clientes ativas.

    Configurar no Coolify: cron diário às 05:00 UTC (antes do push-daily-horoscope)
    com header x-task-secret. Gera horóscopo diário + previsão semanal (se semana nova)
    + guia do mês (se mês novo). Idempotente por período — seguro rodar N vezes.
    """
    secret = os.getenv("TASK_SECRET", "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="TASK_SECRET não configurado.")
    provided = request.headers.get("x-task-secret", "")
    if not hmac.compare_digest(secret.encode(), provided.encode()):
        raise HTTPException(status_code=401, detail="Segredo inválido.")

    stats = run_daily_pregen(db)
    return {"ok": True, **stats}
