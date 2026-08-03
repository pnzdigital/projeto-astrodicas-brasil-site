import hmac
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from .db import get_db
from .models import Entitlement, Order, User
from .pricing import format_amount, title_for
from .ratelimit import auth_rate_limit
from .security import create_token, decode_token

router = APIRouter(prefix="/api/admin", tags=["admin"])

COOKIE_NAME = "admin_session"
PAID_STATUSES = {"paid", "approved"}
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "1") == "1"


def require_admin(admin_session: str | None = Cookie(default=None)) -> str:
    sub = decode_token(admin_session) if admin_session else None
    if sub != "admin":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    return sub


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
    return {"authenticated": decode_token(admin_session) == "admin" if admin_session else False}


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
