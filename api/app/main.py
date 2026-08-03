import hashlib
import hmac
import os
from datetime import date, datetime, time, timezone
from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import Base, engine, get_db
from .astrology import resolve_coordinates
from . import admin, checkout, migrations, pricing
from .ratelimit import auth_rate_limit, webhook_rate_limit
from .engine import generate_reading
from .models import Entitlement, Order, Profile, Reading, User, WebhookEvent
from .security import create_token, decode_token, hash_password, verify_password


SITE_ROOT = Path(__file__).resolve().parents[2]
app = FastAPI(title="AstroDicas Site API", version="1.0.0")
site_origins = [
    origin.strip()
    for origin in os.getenv("SITE_ORIGIN", "http://localhost:8080").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=site_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(checkout.router)
app.include_router(admin.router)


class RegisterBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    name: str = Field(min_length=2, max_length=160)
    locale: str = Field(default="pt-BR", max_length=10)


class LoginBody(BaseModel):
    email: EmailStr
    password: str


class ProfileBody(BaseModel):
    birth_date: date | None = None
    birth_time: time | None = None
    birth_city: str = Field(default="", max_length=160)
    birth_country: str = Field(default="BR", min_length=2, max_length=2)
    birth_timezone: str = Field(default="America/Sao_Paulo", max_length=64)
    birth_latitude: str | None = Field(default=None, max_length=32)
    birth_longitude: str | None = Field(default=None, max_length=32)
    partner_name: str = Field(default="", max_length=160)
    partner_birth_date: date | None = None
    partner_birth_time: time | None = None
    partner_birth_city: str = Field(default="", max_length=160)
    partner_country: str = Field(default="BR", min_length=2, max_length=2)


class WebhookBody(BaseModel):
    event_id: str
    email: EmailStr
    product_id: str
    status: str = "paid"
    amount_minor: int = 0
    currency: str = "BRL"
    external_id: str | None = None


def current_user(session: str | None, db: Session) -> User:
    user_id = decode_token(session or "")
    user = db.get(User, user_id) if user_id else None
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Faça login para continuar.")
    return user


def set_session(response: Response, user: User) -> None:
    response.set_cookie("site_session", create_token(user.id), httponly=True, secure=os.getenv("COOKIE_SECURE", "1") == "1", samesite="lax", max_age=60 * 60 * 24 * 30)


def validate_production_config() -> None:
    """Falha rápido em produção: nunca subir com segredo crítico ausente.

    ``SITE_SECRET_KEY`` já é validado na importação de ``app.security``. Aqui
    cobrimos os demais segredos que hoje só eram checados por rota (503 tardio):
    sem eles em produção, o serviço nem deve terminar o startup.
    """
    if os.getenv("ENV", "development") != "production":
        return
    missing = [
        name
        for name in ("MP_WEBHOOK_SECRET", "CAKTO_WEBHOOK_SECRET", "ADMIN_PASSWORD")
        if not os.getenv(name, "").strip()
    ]
    if missing:
        raise RuntimeError(
            "Configuração insegura para produção: variáveis ausentes -> " + ", ".join(missing)
        )
    if os.getenv("COOKIE_SECURE", "1") != "1":
        raise RuntimeError("Configuração insegura para produção: COOKIE_SECURE precisa ser 1.")


@app.on_event("startup")
def startup() -> None:
    validate_production_config()
    Path("data").mkdir(exist_ok=True)
    Base.metadata.create_all(engine)
    migrations.ensure_schema()


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "service": "astrodicas-site", "channel": "site"}


@app.post("/api/auth/register", dependencies=[Depends(auth_rate_limit)])
def register(body: RegisterBody, response: Response, db: Session = Depends(get_db)) -> dict:
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail="Este e-mail já está cadastrado.")
    user = User(email=email, password_hash=hash_password(body.password), name=body.name, locale=body.locale)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Este e-mail já está cadastrado.")
    db.refresh(user)
    set_session(response, user)
    return {"user": {"id": user.id, "email": user.email, "name": user.name, "locale": user.locale}}


@app.post("/api/auth/login", dependencies=[Depends(auth_rate_limit)])
def login(body: LoginBody, response: Response, db: Session = Depends(get_db)) -> dict:
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="E-mail ou senha inválidos.")
    set_session(response, user)
    return {"user": {"id": user.id, "email": user.email, "name": user.name, "locale": user.locale}}


@app.post("/api/auth/logout")
def logout(response: Response) -> dict:
    response.delete_cookie("site_session")
    return {"ok": True}


def _demo_allowed() -> bool:
    """Demo só abre vitrine se ALLOW_DEMO=1 e ENV != production."""
    if os.getenv("ENV", "development") == "production":
        return False
    return os.getenv("ALLOW_DEMO", "0") == "1"


@app.get("/api/auth-gate")
def auth_gate(demo: str | None = None) -> dict:
    """Gate explícito para o portal decidir se libera vitrine de demo.

    - Sem `demo=paid`: 200 com demo=false (não afeta o fluxo).
    - `demo=paid` + ALLOW_DEMO=1 + ENV != production: 200 com demo=true.
    - `demo=paid` em qualquer outro caso: 403.
    """
    if demo != "paid":
        return {"demo": False}
    if not _demo_allowed():
        raise HTTPException(status_code=403, detail="Demonstração indisponível neste ambiente.")
    return {"demo": True}


@app.get("/api/session")
def session(site_session: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> dict:
    user_id = decode_token(site_session or "")
    user = db.get(User, user_id) if user_id else None
    if not user:
        return {"authenticated": False}
    return {"authenticated": True, "user": {"id": user.id, "email": user.email, "name": user.name, "locale": user.locale}}


@app.get("/api/me/profile")
def get_profile(site_session: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> dict:
    user = current_user(site_session, db)
    profile = db.get(Profile, user.id)
    return {"profile": profile_to_dict(profile) if profile else None}


@app.put("/api/me/profile")
def save_profile(body: ProfileBody, site_session: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> dict:
    user = current_user(site_session, db)
    profile = db.get(Profile, user.id) or Profile(user_id=user.id)
    for key, value in body.model_dump().items():
        setattr(profile, key, value)
    if profile.birth_city and (not profile.birth_latitude or not profile.birth_longitude):
        coordinates = resolve_coordinates(profile.birth_city, profile.birth_country)
        if coordinates:
            profile.birth_latitude, profile.birth_longitude = map(str, coordinates)
    db.add(profile)
    db.commit()
    return {"profile": profile_to_dict(profile)}


@app.get("/api/me/access")
def access(site_session: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> dict:
    user = current_user(site_session, db)
    return {"entitlements": [{"product_id": e.product_id, "status": e.status} for e in user.entitlements]}


@app.get("/api/me/readings")
def readings(site_session: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> dict:
    user = current_user(site_session, db)
    rows = db.scalars(select(Reading).where(Reading.user_id == user.id).order_by(Reading.created_at.desc())).all()
    return {"readings": [reading_to_dict(row) for row in rows]}


@app.post("/api/me/readings/{content_id}/generate")
def generate(content_id: str, site_session: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> dict:
    user = current_user(site_session, db)
    profile = db.get(Profile, user.id)
    if not profile or not profile.birth_date or not profile.birth_city:
        raise HTTPException(status_code=422, detail="Complete seus dados de nascimento antes de gerar a leitura.")
    snapshot = profile_to_dict(profile)
    existing = db.scalar(select(Reading).where(Reading.user_id == user.id, Reading.content_id == content_id, Reading.status == "ready").order_by(Reading.created_at.desc()))
    if existing and reading_is_current(existing, content_id, snapshot):
        return {"reading": reading_to_dict(existing)}
    product_id = content_product(content_id)
    if product_id and not db.scalar(select(Entitlement).where(Entitlement.user_id == user.id, Entitlement.product_id == product_id, Entitlement.status == "available")):
        raise HTTPException(status_code=403, detail="Este conteúdo ainda não está liberado para sua conta.")
    reading = Reading(user_id=user.id, content_id=content_id, product_id=product_id, status="in_progress", title=content_title(content_id), input_snapshot=snapshot)
    db.add(reading)
    db.commit()
    reading.body_html = generate_reading(content_id, reading.title, profile, user.locale, user.name)
    reading.status = "ready"
    db.commit()
    db.refresh(reading)
    return {"reading": reading_to_dict(reading)}


@app.post("/api/webhooks/{provider}", dependencies=[Depends(webhook_rate_limit)])
async def webhook(provider: str, body: WebhookBody, request: Request, db: Session = Depends(get_db)) -> dict:
    if provider not in {"cakto", "mercadopago"}:
        raise HTTPException(status_code=404, detail="Provedor não configurado.")
    secret_env = "CAKTO_WEBHOOK_SECRET" if provider == "cakto" else "MP_WEBHOOK_SECRET"
    secret = os.getenv(secret_env, "").strip()
    env = os.getenv("ENV", "development")
    allow_insecure = os.getenv("ALLOW_INSECURE_DEV", "0") == "1"
    signature = request.headers.get("x-site-signature", "")
    raw = await request.body()
    if not secret:
        if env == "production" or not allow_insecure:
            raise HTTPException(
                status_code=503,
                detail=f"Webhook para {provider} indisponível: segredo não configurado.",
            )
    elif not hmac.compare_digest(signature, hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()):
        raise HTTPException(status_code=401, detail="Assinatura inválida.")
    event = db.scalar(select(WebhookEvent).where(WebhookEvent.provider == provider, WebhookEvent.event_id == body.event_id))
    if event:
        return {"ok": True, "duplicate": True}
    event = WebhookEvent(provider=provider, event_id=body.event_id, payload=body.model_dump(mode="json"))
    db.add(event)
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if not user:
        user = User(email=body.email.lower(), password_hash=hash_password(os.urandom(18).hex()), name="Cliente AstroDicas")
        db.add(user)
        db.flush()
    order = Order(user_id=user.id, provider=provider, external_id=body.external_id or body.event_id, product_id=body.product_id, status=body.status, amount_minor=body.amount_minor, currency=body.currency, customer_email=user.email, raw_payload=body.model_dump(mode="json"))
    db.add(order)
    for product_id in pricing.granted_products(body.product_id):
        entitlement = db.scalar(select(Entitlement).where(Entitlement.user_id == user.id, Entitlement.product_id == product_id))
        if not entitlement:
            db.add(Entitlement(user_id=user.id, product_id=product_id, status="available", source="site"))
        else:
            entitlement.status = "available"
    db.commit()
    return {"ok": True, "user_id": user.id, "product_id": body.product_id}


def content_product(content_id: str) -> str | None:
    return {
        "site:content:horoscopo_diario": "site:plano_lua",
        "site:content:guia_do_mes": "site:plano_lua",
        "site:content:mapa_astral_completo": "site:mapa_astral",
        "site:content:mapa_do_amor_sinastria": "site:mapa_amor_sinastria",
        "site:content:mapa_da_carreira": "site:mapa_carreira",
        "site:content:mapa_da_prosperidade": "site:mapa_prosperidade",
        "site:content:previsao_semanal": "site:oferta_plano_lua_premium",
        "site:content:calendario_lunar": "site:oferta_plano_lua_premium",
        "site:content:guia_dos_retrogrados": "site:oferta_plano_lua_premium",
        "site:content:manual_do_ascendente": "site:oferta_plano_lua_premium",
    }.get(content_id)


def content_title(content_id: str) -> str:
    return {
        "site:content:horoscopo_diario": "Horóscopo diário",
        "site:content:guia_do_mes": "Guia do mês",
        "site:content:mapa_astral_completo": "Mapa Astral Completo",
        "site:content:mapa_do_amor_sinastria": "Mapa do Amor / Sinastria",
        "site:content:mapa_da_carreira": "Mapa da Carreira",
        "site:content:mapa_da_prosperidade": "Mapa da Prosperidade",
        "site:content:previsao_semanal": "Previsão da semana",
        "site:content:calendario_lunar": "Calendário Lunar",
        "site:content:guia_dos_retrogrados": "Guia dos Retrógrados",
        "site:content:manual_do_ascendente": "Manual do Ascendente",
    }.get(content_id, "Leitura AstroDicas")


def reading_is_current(reading: Reading, content_id: str, snapshot: dict) -> bool:
    if reading.input_snapshot != snapshot:
        return False
    created = reading.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if content_id == "site:content:horoscopo_diario":
        return created.date() == now.date()
    if content_id == "site:content:previsao_semanal":
        return created.isocalendar()[:2] == now.isocalendar()[:2]
    if content_id in {"site:content:guia_do_mes", "site:content:calendario_lunar"}:
        return (created.year, created.month) == (now.year, now.month)
    return True


def profile_to_dict(profile: Profile | None) -> dict | None:
    if not profile:
        return None
    return {key: getattr(profile, key).isoformat() if isinstance(getattr(profile, key), (date, time)) else getattr(profile, key) for key in ("user_id", "birth_date", "birth_time", "birth_city", "birth_country", "birth_timezone", "birth_latitude", "birth_longitude", "partner_name", "partner_birth_date", "partner_birth_time", "partner_birth_city", "partner_country")}


def reading_to_dict(reading: Reading) -> dict:
    return {"id": reading.id, "content_id": reading.content_id, "product_id": reading.product_id, "status": reading.status, "title": reading.title, "body_html": reading.body_html, "created_at": reading.created_at.isoformat(), "updated_at": reading.updated_at.isoformat()}


async def await_request_body(request: Request) -> bytes:
    return await request.body()
