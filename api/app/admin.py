import hmac
import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import engine as engine_module
from .db import get_db
from .models import Entitlement, GenerationJob, Order, Reading, Subscription, User
from .pricing import format_amount, title_for
from .ratelimit import auth_rate_limit
from .security import create_token, decode_token
from .worker import enqueue_generation_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

COOKIE_NAME = "admin_session"
PAID_STATUSES = {"paid", "approved"}
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "1") == "1"


def require_admin(admin_session: str | None = Cookie(default=None)) -> str:
    payload = decode_token(admin_session) if admin_session else None
    if not payload or payload["user_id"] != "admin":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    return payload["user_id"]


@router.post("/login", dependencies=[Depends(auth_rate_limit)])
def login(body: dict, response: Response) -> dict:
    admin_password = os.getenv("ADMIN_PASSWORD")
    if not admin_password:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="admin login not configured")
    password = str(body.get("password") or "")
    if not hmac.compare_digest(password, admin_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid password")
    response.set_cookie(
        COOKIE_NAME,
        create_token("admin"),
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return {"ok": True}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.get("/session")
def session(admin_session: str | None = Cookie(default=None)) -> dict:
    payload = decode_token(admin_session) if admin_session else None
    return {"authenticated": bool(payload and payload["user_id"] == "admin")}


@router.get("/sales")
def sales(
    market: str | None = None,
    status: str | None = None,
    from_: str | None = None,
    to: str | None = None,
    limit: int = 50,
    offset: int = 0,
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    query = db.query(Order)
    if market:
        query = query.filter(Order.market == market)
    if status:
        query = query.filter(Order.status == status)
    if from_:
        query = query.filter(Order.created_at >= datetime.fromisoformat(from_))
    if to:
        query = query.filter(Order.created_at <= datetime.fromisoformat(to))
    total = query.count()
    orders = query.order_by(Order.created_at.desc()).offset(offset).limit(limit).all()
    rows = [
        {
            "id": order.id,
            "created_at": order.created_at.isoformat(),
            "customer_email": order.customer_email,
            "product_id": order.product_id,
            "title": title_for(order.product_id, order.locale),
            "status": order.status,
            "provider": order.provider,
            "amount_minor": order.amount_minor,
            "amount_label": format_amount(order.amount_minor, order.currency),
            "currency": order.currency,
            "locale": order.locale,
            "market": order.market,
        }
        for order in orders
    ]
    return {"total": total, "limit": limit, "offset": offset, "sales": rows}


@router.get("/summary")
def summary(_admin: str = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    orders = db.query(Order).all()

    revenue_by_market: dict[str, dict] = {}
    by_product: dict[str, dict] = {}
    by_status: dict[str, dict] = {}
    by_provider: dict[str, dict] = {}
    daily: dict[str, int] = {}

    since = datetime.now(timezone.utc) - timedelta(days=30)
    for i in range(30):
        day = (since + timedelta(days=i)).date().isoformat()
        daily[day] = 0

    for order in orders:
        # Receita conta somente venda paga: ordem pendente ou recusada é funil,
        # não faturamento. A contagem total continua visível em `by_status`.
        paid = order.status in PAID_STATUSES
        market = order.market
        bucket = revenue_by_market.setdefault(
            market,
            {"currency": order.currency, "sales_count": 0, "revenue_minor": 0, "orders_count": 0},
        )
        bucket["orders_count"] += 1
        if paid:
            bucket["sales_count"] += 1
            bucket["revenue_minor"] += order.amount_minor

        prod = by_product.setdefault(order.product_id, {"sales_count": 0, "orders_count": 0, "revenue_minor_by_currency": {}})
        prod["orders_count"] += 1
        if paid:
            prod["sales_count"] += 1
            prod["revenue_minor_by_currency"][order.currency] = (
                prod["revenue_minor_by_currency"].get(order.currency, 0) + order.amount_minor
            )

        st = by_status.setdefault(order.status, {"count": 0})
        st["count"] += 1

        prov = by_provider.setdefault(order.provider, {"count": 0})
        prov["count"] += 1

        created = order.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        day = created.date().isoformat()
        if day in daily:
            daily[day] += 1

    for market, bucket in revenue_by_market.items():
        bucket["revenue_label"] = format_amount(bucket["revenue_minor"], bucket["currency"])

    daily_series = [{"date": day, "sales_count": count} for day, count in sorted(daily.items())]

    users_count = db.query(func.count(User.id)).scalar() or 0
    active_entitlements = db.query(func.count(Entitlement.id)).filter(Entitlement.status == "available").scalar() or 0

    return {
        "revenue_by_market": revenue_by_market,
        "by_product": by_product,
        "by_status": by_status,
        "by_provider": by_provider,
        "daily_series": daily_series,
        "users_count": users_count,
        "active_entitlements": active_entitlements,
    }


@router.get("/trials")
def trials(
    limit: int = 100,
    offset: int = 0,
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Lista assinantes em período de trial (provider=none, status=trialing)."""
    query = (
        db.query(Subscription, User)
        .join(User, User.id == Subscription.user_id)
        .filter(Subscription.status == "trialing")
        .order_by(Subscription.created_at.desc())
    )
    total = query.count()
    rows_raw = query.offset(offset).limit(limit).all()
    now = datetime.now(timezone.utc)

    rows = []
    for sub, user in rows_raw:
        trial_ends = sub.trial_ends_at
        if trial_ends and trial_ends.tzinfo is None:
            trial_ends = trial_ends.replace(tzinfo=timezone.utc)
        expired = trial_ends < now if trial_ends else False
        rows.append({
            "subscription_id": sub.id,
            "user_email": user.email,
            "user_name": user.name,
            "product_id": sub.product_id,
            "status": sub.status,
            "created_at": sub.created_at.isoformat() if sub.created_at else None,
            "trial_ends_at": trial_ends.isoformat() if trial_ends else None,
            "trial_expired": expired,
            "provider": sub.provider,
        })

    return {"total": total, "limit": limit, "offset": offset, "trials": rows}


@router.get("/users/search")
def users_search(
    email: str,
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Localiza usuário por e-mail (busca exata, case-insensitive)."""
    user = db.scalars(select(User).where(func.lower(User.email) == email.strip().lower())).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "locale": user.locale,
        "created_at": user.created_at.isoformat(),
    }


@router.get("/users/{user_id}/readings")
def user_readings(
    user_id: str,
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Lista leituras de um usuário com status, fonte e custo estimado de regeração."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    readings = (
        db.scalars(
            select(Reading)
            .where(Reading.user_id == user_id)
            .order_by(Reading.created_at.desc())
        )
        .all()
    )
    rows = []
    for r in readings:
        sections = engine_module.sections_for(r.content_id)
        estimated_requests = len(sections) if sections else 1
        rows.append({
            "id": r.id,
            "content_id": r.content_id,
            "title": r.title,
            "status": r.status,
            "source": r.source,
            "is_fallback": r.status == "fallback" or r.source == "fallback",
            "error_message": r.error_message,
            "sections_done": r.sections_done,
            "sections_total": r.sections_total,
            "created_at": r.created_at.isoformat(),
            "updated_at": r.updated_at.isoformat(),
            "estimated_requests": estimated_requests,
        })
    return {"user_id": user_id, "readings": rows}


@router.post("/readings/{reading_id}/regenerate")
def regenerate_reading(
    reading_id: str,
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Invalida leitura existente e dispara nova geração.

    Segurança:
    - Sessão admin obrigatória (require_admin).
    - Idempotente por job ativo: se já houver job queued/running para
      (user_id, content_id), retorna 409 em vez de enfileirar duplicata.
    - Leitura anterior marcada como 'superseded' (não destruída) para auditoria.
    - Cada ação é registrada em log com admin_id, reading_id e user_id.
    """
    reading = db.get(Reading, reading_id)
    if not reading:
        raise HTTPException(status_code=404, detail="Leitura não encontrada.")

    # Bloqueia duplo clique / reenfileiramento enquanto job ativo existe.
    active_job = db.scalars(
        select(GenerationJob).where(
            GenerationJob.user_id == reading.user_id,
            GenerationJob.content_id == reading.content_id,
            GenerationJob.status.in_(["queued", "running"]),
        )
    ).first()
    if active_job:
        raise HTTPException(
            status_code=409,
            detail=f"Já existe um job ativo ({active_job.status}) para esta leitura. Aguarde terminar antes de regerar.",
        )

    user = db.get(User, reading.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuário da leitura não encontrado.")

    # Preserva leitura anterior como 'superseded' (mantém body_html, source, etc.).
    reading.status = "superseded"
    db.flush()

    # Cria nova leitura em branco para receber o resultado da geração.
    sections = engine_module.sections_for(reading.content_id)
    new_reading = Reading(
        user_id=reading.user_id,
        content_id=reading.content_id,
        product_id=reading.product_id,
        status="pending",
        title=reading.title,
        source="llm",
        reading_kind=reading.reading_kind,
        input_snapshot=reading.input_snapshot,
        sections_total=len(sections),
        sections_done=0,
    )
    db.add(new_reading)
    db.commit()
    db.refresh(new_reading)

    # enqueue_generation_job abre sessão própria; chamamos só após commit
    # para evitar lock no SQLite (dev) e deadlock no Postgres (prod).
    job = enqueue_generation_job(
        reading_id=new_reading.id,
        content_id=reading.content_id,
        user_id=reading.user_id,
        locale=user.locale,
        customer_name=user.name,
    )

    estimated_requests = len(sections) if sections else 1
    logger.info(
        "admin_regenerate admin=%s reading_id=%s new_reading_id=%s user_id=%s content_id=%s estimated_requests=%d",
        _admin,
        reading_id,
        new_reading.id,
        reading.user_id,
        reading.content_id,
        estimated_requests,
    )

    return {
        "ok": True,
        "superseded_reading_id": reading_id,
        "new_reading_id": new_reading.id,
        "job_id": job.id if job else None,
        "estimated_requests": estimated_requests,
    }


@router.get("/quota")
def quota(_admin: str = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    """Consumo de requisições/tokens MiniMax da semana ISO corrente, por
    modelo, contra o teto configurável (MINIMAX_WEEKLY_REQUEST_LIMIT)."""
    return engine_module.get_weekly_quota_snapshot(db)
