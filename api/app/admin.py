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
from .pricing import PRICES_BRL_MINOR, format_amount, title_for
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


# ---------------------------------------------------------------------------
# Painel de custo — análise de margem por produto
# ---------------------------------------------------------------------------
#
# Preços padrão MiniMax-M2.7 (pay-as-you-go, tabela pública):
#   entrada: US$ 0,30/M tokens · saída: US$ 1,20/M tokens
#   (o bloco <think> do M2.7 é cobrado como saída — invisível p/ cliente, pago)
# Fonte: openrouter.ai/minimax/minimax-m2.7, pricepertoken.com, nerova.ai
# Verificado em 2026-08-13 — preços de tabela envelhecem; confira antes de decidir.
#
# Estimativa por seção: ~1.500 tokens input + ~2.000 output ≈ US$ 0,00285/seção
#
# Envs configuráveis (todas opcionais — defaults abaixo se ausentes):
#   MINIMAX_USD_PER_1M_INPUT_TOKENS   (default 0.30)
#   MINIMAX_USD_PER_1M_OUTPUT_TOKENS  (default 1.20)
#   MINIMAX_EST_INPUT_TOKENS_PER_SECTION  (default 1500)
#   MINIMAX_EST_OUTPUT_TOKENS_PER_SECTION (default 2000)
#   MINIMAX_PLAN_USD_PER_MONTH  — mensalidade do plano (sem default; modo b)
#   COST_USD_BRL_RATE           — câmbio USD→BRL (sem default; mostrado na tela)

_DEFAULT_USD_PER_1M_IN: float = 0.30
_DEFAULT_USD_PER_1M_OUT: float = 1.20
_DEFAULT_EST_IN_PER_SECTION: float = 1500
_DEFAULT_EST_OUT_PER_SECTION: float = 2000

# Mapeamento produto → conteúdo gerado × frequência em 30 dias.
# Tupla: (content_id, vezes_por_30_dias, recorrente)
# recorrente=True: gerado todo mês; False: geração única (bundle, one-time).
_PRODUCT_CYCLE_PLAN: dict[str, list[tuple[str, int, bool]]] = {
    "site:diario_astral": [
        ("site:content:horoscopo_diario", 30, True),
        ("site:content:guia_do_mes", 1, True),
        ("site:content:previsao_semanal", 4, True),
        ("site:content:mapa_astral_completo", 1, False),  # brinde do 1º mês
    ],
    "site:mapa_astral": [
        ("site:content:mapa_astral_completo", 1, False),
    ],
    "site:mapa_amor_sinastria": [
        ("site:content:mapa_do_amor_sinastria", 1, False),
    ],
    "site:mapa_carreira": [
        ("site:content:mapa_da_carreira", 1, False),
    ],
    "site:mapa_prosperidade": [
        ("site:content:mapa_da_prosperidade", 1, False),
    ],
    "site:diario_astral_completo": [
        ("site:content:horoscopo_diario", 30, True),
        ("site:content:guia_do_mes", 1, True),
        ("site:content:previsao_semanal", 4, True),
        ("site:content:calendario_lunar", 1, True),
        ("site:content:guia_dos_retrogrados", 1, True),
        ("site:content:mapa_astral_completo", 1, False),
        ("site:content:mapa_do_amor_sinastria", 1, False),
        ("site:content:mapa_da_prosperidade", 1, False),
        ("site:content:manual_do_ascendente", 1, False),
    ],
    "site:combo_mapa_astral_amor": [
        ("site:content:mapa_astral_completo", 1, False),
        ("site:content:mapa_do_amor_sinastria", 1, False),
    ],
    "site:combo_mapa_astral_carreira": [
        ("site:content:mapa_astral_completo", 1, False),
        ("site:content:mapa_da_carreira", 1, False),
    ],
    "site:combo_mapa_astral_prosperidade": [
        ("site:content:mapa_astral_completo", 1, False),
        ("site:content:mapa_da_prosperidade", 1, False),
    ],
    "site:combo_amor_carreira": [
        ("site:content:mapa_do_amor_sinastria", 1, False),
        ("site:content:mapa_da_carreira", 1, False),
    ],
    "site:combo_amor_prosperidade": [
        ("site:content:mapa_do_amor_sinastria", 1, False),
        ("site:content:mapa_da_prosperidade", 1, False),
    ],
    "site:combo_carreira_prosperidade": [
        ("site:content:mapa_da_carreira", 1, False),
        ("site:content:mapa_da_prosperidade", 1, False),
    ],
    "site:combo_diario_astral_mapa_astral": [
        ("site:content:horoscopo_diario", 30, True),
        ("site:content:guia_do_mes", 1, True),
        ("site:content:previsao_semanal", 4, True),
        ("site:content:mapa_astral_completo", 1, False),
    ],
    "site:combo_diario_astral_mapa_amor": [
        ("site:content:horoscopo_diario", 30, True),
        ("site:content:guia_do_mes", 1, True),
        ("site:content:previsao_semanal", 4, True),
        ("site:content:mapa_do_amor_sinastria", 1, False),
    ],
    "site:combo_diario_astral_mapa_prosperidade": [
        ("site:content:horoscopo_diario", 30, True),
        ("site:content:guia_do_mes", 1, True),
        ("site:content:previsao_semanal", 4, True),
        ("site:content:mapa_da_prosperidade", 1, False),
    ],
    "site:diario_astral_completo_bump": [
        ("site:content:horoscopo_diario", 30, True),
        ("site:content:guia_do_mes", 1, True),
        ("site:content:previsao_semanal", 4, True),
        ("site:content:calendario_lunar", 1, True),
        ("site:content:guia_dos_retrogrados", 1, True),
        ("site:content:mapa_astral_completo", 1, False),
        ("site:content:mapa_do_amor_sinastria", 1, False),
        ("site:content:mapa_da_prosperidade", 1, False),
        ("site:content:manual_do_ascendente", 1, False),
    ],
    "site:diario_astral_oferta_saida": [
        ("site:content:horoscopo_diario", 30, True),
        ("site:content:guia_do_mes", 1, True),
        ("site:content:previsao_semanal", 4, True),
        ("site:content:mapa_astral_completo", 1, False),
    ],
}


def _parse_float_env(name: str, default: float | None = None) -> float | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _cost_per_section_usd() -> float:
    price_in = _parse_float_env("MINIMAX_USD_PER_1M_INPUT_TOKENS", _DEFAULT_USD_PER_1M_IN)
    price_out = _parse_float_env("MINIMAX_USD_PER_1M_OUTPUT_TOKENS", _DEFAULT_USD_PER_1M_OUT)
    est_in = _parse_float_env("MINIMAX_EST_INPUT_TOKENS_PER_SECTION", _DEFAULT_EST_IN_PER_SECTION)
    est_out = _parse_float_env("MINIMAX_EST_OUTPUT_TOKENS_PER_SECTION", _DEFAULT_EST_OUT_PER_SECTION)
    return (est_in * price_in + est_out * price_out) / 1_000_000  # type: ignore[operator]


def _content_title(content_id: str) -> str:
    return {
        "site:content:horoscopo_diario": "Horóscopo Diário",
        "site:content:guia_do_mes": "Guia do Mês",
        "site:content:mapa_astral_completo": "Mapa Astral Completo",
        "site:content:mapa_do_amor_sinastria": "Mapa do Amor / Sinastria",
        "site:content:mapa_da_carreira": "Mapa da Carreira",
        "site:content:mapa_da_prosperidade": "Mapa da Prosperidade",
        "site:content:previsao_semanal": "Previsão Semanal",
        "site:content:calendario_lunar": "Calendário Lunar",
        "site:content:guia_dos_retrogrados": "Guia dos Retrógrados",
        "site:content:manual_do_ascendente": "Manual do Ascendente",
    }.get(content_id, content_id)


def _content_cost_table() -> dict[str, dict]:
    cost_per_sec = _cost_per_section_usd()
    usd_brl = _parse_float_env("COST_USD_BRL_RATE")
    table: dict[str, dict] = {}
    for content_id, sections in engine_module.SECTIONS_BY_CONTENT_ID.items():
        n = len(sections)
        is_long = content_id in engine_module._LONG_CONTENT_IDS
        budget_per_section = (
            engine_module._SECTION_TOKEN_BUDGET_M3 if is_long
            else engine_module._SECTION_TOKEN_BUDGET
        )
        cost_usd = n * cost_per_sec
        cost_brl = round(cost_usd * usd_brl, 4) if usd_brl is not None else None
        table[content_id] = {
            "sections": n,
            "req_per_generation": n,
            "max_tokens_budget_per_generation": n * budget_per_section,
            "model": "MiniMax-M3" if is_long else "MiniMax-M2.7",
            "est_cost_usd_per_generation": round(cost_usd, 6),
            "est_cost_brl_per_generation": cost_brl,
        }
    return table


def _plan_cost_per_req(weekly_quota: dict) -> float | None:
    plan_usd = _parse_float_env("MINIMAX_PLAN_USD_PER_MONTH")
    if plan_usd is None:
        return None
    weekly_limit = weekly_quota.get("limit", 0)
    if weekly_limit <= 0:
        return None
    monthly_capacity = weekly_limit * (365 / 12 / 7)  # ≈ 4,333 semanas/mês
    return plan_usd / monthly_capacity


def cost_catalog(db=None) -> dict:
    """Custo estimado por conteúdo e produto, com margem.

    Distingue ESTIMADO (modo token, a partir de budget) de MEDIDO (weekly_quota).
    Dois modos de custo:
      (a) token — preços de tabela MiniMax pay-as-you-go (defaults embutidos)
      (b) plano — mensalidade ÷ capacidade mensal (requer MINIMAX_PLAN_USD_PER_MONTH)
    """
    usd_brl = _parse_float_env("COST_USD_BRL_RATE")
    weekly_quota = engine_module.get_weekly_quota_snapshot(db)
    plan_per_req = _plan_cost_per_req(weekly_quota)
    content_table = _content_cost_table()

    price_in = _parse_float_env("MINIMAX_USD_PER_1M_INPUT_TOKENS", _DEFAULT_USD_PER_1M_IN)
    price_out = _parse_float_env("MINIMAX_USD_PER_1M_OUTPUT_TOKENS", _DEFAULT_USD_PER_1M_OUT)
    est_in = _parse_float_env("MINIMAX_EST_INPUT_TOKENS_PER_SECTION", _DEFAULT_EST_IN_PER_SECTION)
    est_out = _parse_float_env("MINIMAX_EST_OUTPUT_TOKENS_PER_SECTION", _DEFAULT_EST_OUT_PER_SECTION)
    plan_usd = _parse_float_env("MINIMAX_PLAN_USD_PER_MONTH")

    contents = []
    for content_id, data in content_table.items():
        n = data["req_per_generation"]
        plan_cost = round(n * plan_per_req, 6) if plan_per_req is not None else None
        contents.append({
            "content_id": content_id,
            "title": _content_title(content_id),
            **data,
            "plan_cost_usd_per_generation": plan_cost,
        })

    products = []
    for product_id in PRICES_BRL_MINOR:
        price_brl_minor = PRICES_BRL_MINOR[product_id]
        cycle_plan = _PRODUCT_CYCLE_PLAN.get(product_id, [])

        cycle_req_recurring = 0
        cycle_req_onetime = 0
        tok_recurring = 0.0
        tok_onetime = 0.0
        plan_recurring: float | None = 0.0 if plan_per_req is not None else None
        plan_onetime: float | None = 0.0 if plan_per_req is not None else None
        breakdown = []

        for cid, times, recurring in cycle_plan:
            ct = content_table.get(cid)
            if ct is None:
                continue
            n = ct["req_per_generation"]
            req = n * times
            tok = ct["est_cost_usd_per_generation"] * times
            p = (n * plan_per_req * times) if plan_per_req is not None else None
            breakdown.append({
                "content_id": cid,
                "title": _content_title(cid),
                "times_per_30d": times,
                "recurring": recurring,
                "req": req,
                "est_cost_token_usd": round(tok, 6),
                "est_cost_plan_usd": round(p, 6) if p is not None else None,
            })
            if recurring:
                cycle_req_recurring += req
                tok_recurring += tok
                if plan_recurring is not None and p is not None:
                    plan_recurring += p
            else:
                cycle_req_onetime += req
                tok_onetime += tok
                if plan_onetime is not None and p is not None:
                    plan_onetime += p

        tok_total = tok_recurring + tok_onetime
        plan_total = (plan_recurring + plan_onetime) if (plan_recurring is not None and plan_onetime is not None) else None

        def _brl(usd: float | None) -> float | None:
            return round(usd * usd_brl, 4) if (usd is not None and usd_brl is not None) else None  # noqa: B023

        price_brl = price_brl_minor / 100

        def _margin(cost_brl: float | None) -> tuple[float | None, float | None]:
            if cost_brl is None:
                return None, None
            m = round(price_brl - cost_brl, 2)  # noqa: B023
            p = round((m / price_brl) * 100, 1) if price_brl > 0 else None  # noqa: B023
            return m, p

        tok_brl = _brl(tok_total)
        plan_brl = _brl(plan_total)
        margin_tok_brl, margin_tok_pct = _margin(tok_brl)
        margin_plan_brl, margin_plan_pct = _margin(plan_brl)

        products.append({
            "product_id": product_id,
            "title": title_for(product_id, "pt-BR"),
            "price_brl_minor": price_brl_minor,
            "price_label": format_amount(price_brl_minor, "BRL"),
            "cycle_req_recurring": cycle_req_recurring,
            "cycle_req_onetime": cycle_req_onetime,
            "cycle_req_total": cycle_req_recurring + cycle_req_onetime,
            "token_cost_usd_recurring": round(tok_recurring, 6),
            "token_cost_usd_onetime": round(tok_onetime, 6),
            "token_cost_usd_total": round(tok_total, 6),
            "token_cost_brl_total": tok_brl,
            "margin_token_brl": margin_tok_brl,
            "margin_token_pct": margin_tok_pct,
            "plan_cost_usd_recurring": round(plan_recurring, 6) if plan_recurring is not None else None,
            "plan_cost_usd_onetime": round(plan_onetime, 6) if plan_onetime is not None else None,
            "plan_cost_usd_total": round(plan_total, 6) if plan_total is not None else None,
            "plan_cost_brl_total": plan_brl,
            "margin_plan_brl": margin_plan_brl,
            "margin_plan_pct": margin_plan_pct,
            "breakdown": breakdown,
        })

    env_status = {
        "MINIMAX_USD_PER_1M_INPUT_TOKENS": _parse_float_env("MINIMAX_USD_PER_1M_INPUT_TOKENS") is not None,
        "MINIMAX_USD_PER_1M_OUTPUT_TOKENS": _parse_float_env("MINIMAX_USD_PER_1M_OUTPUT_TOKENS") is not None,
        "MINIMAX_EST_INPUT_TOKENS_PER_SECTION": _parse_float_env("MINIMAX_EST_INPUT_TOKENS_PER_SECTION") is not None,
        "MINIMAX_EST_OUTPUT_TOKENS_PER_SECTION": _parse_float_env("MINIMAX_EST_OUTPUT_TOKENS_PER_SECTION") is not None,
        "MINIMAX_PLAN_USD_PER_MONTH": plan_usd is not None,
        "COST_USD_BRL_RATE": usd_brl is not None,
    }

    return {
        "env_configured": env_status,
        "effective_params": {
            "usd_per_1m_input": price_in,
            "usd_per_1m_output": price_out,
            "est_input_tokens_per_section": est_in,
            "est_output_tokens_per_section": est_out,
            "usd_per_section": round(_cost_per_section_usd(), 8),
            "plan_usd_per_month": plan_usd,
            "usd_brl_rate": usd_brl,
        },
        "pricing_note": (
            "Modo (a) usa preços de tabela MiniMax pay-as-you-go (openrouter.ai, 2026-08-13). "
            "O bloco <think> do M2.7 é cobrado como saída — custo real pode ser maior que o texto entregue. "
            "Modo (b) requer MINIMAX_PLAN_USD_PER_MONTH. "
            "Câmbio USD→BRL configurável via COST_USD_BRL_RATE — valor chumbado envelhece sem avisar."
        ),
        "contents": contents,
        "products": products,
        "weekly_quota": weekly_quota,
    }


@router.get("/cost")
def cost(_admin: str = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    """Análise de custo por conteúdo e produto (ESTIMADO) + consumo real (MEDIDO).

    ESTIMADO = cálculo a partir de budget de tokens e preços de tabela MiniMax.
    MEDIDO = weekly_quota snapshot (persistido em site_quota_usage).
    Não misture os dois numa decisão de preço — são fontes distintas.
    """
    return cost_catalog(db)
