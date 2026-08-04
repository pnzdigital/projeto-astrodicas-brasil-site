import hashlib
import hmac
import json
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import Base, engine, get_db
from .astrology import resolve_coordinates
from . import admin, checkout, migrations, preview, pricing
from .ratelimit import auth_rate_limit, password_reset_rate_limit, webhook_rate_limit
from .engine import generate_reading
from .models import Entitlement, Order, PasswordResetToken, Profile, Reading, User, WebhookEvent
from .security import (
    create_token,
    decode_token,
    hash_password,
    hash_reset_token,
    new_reset_token,
    verify_password,
)


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
app.include_router(preview.router)


# --- Localized user-facing copy ----------------------------------------------
# Mantém o backend agnóstico de i18n da UI: cada mensagem vive em pt-BR e es-AR.
# A UI pode passar `locale` no corpo (register/login) e o backend escolhe. Em
# rotas autenticadas onde o payload é vazio, usamos Accept-Language; como
# fallback, pt-BR.
AUTH_MESSAGES: dict[str, dict[str, str]] = {
    "login_invalid": {
        "pt-BR": "E-mail ou senha inválidos.",
        "es-AR": "E-mail o contraseña inválidos.",
    },
    "register_email_taken": {
        "pt-BR": "Este e-mail já está cadastrado.",
        "es-AR": "Este e-mail ya está registrado.",
    },
    "validation_required": {
        "pt-BR": "Preencha e-mail, senha (mín. 8 caracteres) e nome.",
        "es-AR": "Completá e-mail, contraseña (mín. 8 caracteres) y nombre.",
    },
    "validation_email": {
        "pt-BR": "Informe um e-mail válido.",
        "es-AR": "Ingresá un e-mail válido.",
    },
    "validation_password": {
        "pt-BR": "A senha precisa ter pelo menos 8 caracteres.",
        "es-AR": "La contraseña debe tener al menos 8 caracteres.",
    },
    "validation_name": {
        "pt-BR": "Informe seu nome (mín. 2 caracteres).",
        "es-AR": "Ingresá tu nombre (mín. 2 caracteres).",
    },
    "session_required": {
        "pt-BR": "Faça login para continuar.",
        "es-AR": "Iniciá sesión para continuar.",
    },
    "rate_limited": {
        "pt-BR": "Muitas tentativas. Tente novamente em instantes.",
        "es-AR": "Demasiados intentos. Probá de nuevo en unos instantes.",
    },
    "reset_request_ok": {
        "pt-BR": "Se o e-mail estiver cadastrado, enviaremos um link para redefinir sua senha.",
        "es-AR": "Si el e-mail está registrado, te enviaremos un enlace para restablecer tu contraseña.",
    },
    "reset_token_invalid": {
        "pt-BR": "Este link expirou ou já foi usado. Solicite um novo.",
        "es-AR": "Este enlace expiró o ya fue usado. Solicitá uno nuevo.",
    },
    "reset_password_too_short": {
        "pt-BR": "A nova senha precisa ter pelo menos 8 caracteres.",
        "es-AR": "La nueva contraseña debe tener al menos 8 caracteres.",
    },
    "reset_done": {
        "pt-BR": "Senha redefinida. Entre com a nova senha.",
        "es-AR": "Contraseña restablecida. Iniciá sesión con la nueva.",
    },
}

SUPPORTED_LOCALES = {"pt-BR", "es-AR"}


def _pick_locale(locale: str | None, accept_language: str | None = None) -> str:
    if locale and locale in SUPPORTED_LOCALES:
        return locale
    if accept_language:
        primary = accept_language.split(",")[0].split(";")[0].strip()
        if primary in SUPPORTED_LOCALES:
            return primary
        if primary.startswith("es"):
            return "es-AR"
        if primary.startswith("pt"):
            return "pt-BR"
    return "pt-BR"


def _msg(key: str, locale: str | None, accept_language: str | None = None) -> str:
    table = AUTH_MESSAGES.get(key) or AUTH_MESSAGES["session_required"]
    return table[_pick_locale(locale, accept_language)]


def _auth_validation_detail(errors, locale: str, accept_language: str | None) -> str:
    """Traduz o primeiro erro de validação dos formulários de auth.

    Pydantic v2 retorna uma lista de erros com ``loc`` apontando o campo e
    um ``type``. Mapeamos para uma única frase humana na língua do usuário
    — sem despejar o array JSON no cliente (vazava estrutura interna).
    """
    accept_language = accept_language or ""
    for err in errors:
        loc = err.get("loc") or []
        field = loc[-1] if loc else ""
        etype = err.get("type") or ""
        if field == "email" or "email" in etype:
            return _msg("validation_email", locale, accept_language)
        if field == "password" or "string_too_short" in etype and "password" in str(loc):
            return _msg("validation_password", locale, accept_language)
        if field == "name":
            return _msg("validation_name", locale, accept_language)
        if field == "password" or "string_too_short" in etype:
            return _msg("validation_password", locale, accept_language)
    return _msg("validation_required", locale, accept_language)


@app.exception_handler(RequestValidationError)
async def auth_validation_handler(request: Request, exc: RequestValidationError) -> Response:
    """Localiza validação para rotas de auth e da prévia. Outras seguem default."""
    path = request.url.path
    if not path.startswith("/api/auth/") and not path.startswith("/api/preview/"):
        # Fallback para o handler padrão do FastAPI: 422 com array.
        from fastapi.exception_handlers import request_validation_exception_handler
        return await request_validation_exception_handler(request, exc)
    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    body_locale = body.get("locale") if isinstance(body, dict) else None
    accept_language = request.headers.get("accept-language")
    if path.startswith("/api/preview/"):
        # A prévia tem seu próprio dicionário de mensagens (campos de nascimento,
        # não de credenciais), então delega para ele em vez de AUTH_MESSAGES.
        locale = preview.pick_locale(body_locale, accept_language)
        detail = preview.validation_detail(exc.errors(), locale)
    else:
        locale = _pick_locale(body_locale, accept_language)
        detail = _auth_validation_detail(exc.errors(), locale, accept_language)
    return Response(
        media_type="application/json",
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=json.dumps({"detail": detail}, ensure_ascii=False),
    )


class RegisterBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    name: str = Field(min_length=2, max_length=160)
    # Sem default fixo: a UI envia explicitamente; o backend usa Accept-Language
    # como fallback quando ausente (consistente com o login).
    locale: str | None = Field(default=None, max_length=10)


class LoginBody(BaseModel):
    email: EmailStr
    password: str
    # Opcional e sem default: se não vier no body, aceitamos o fallback do
    # header ``Accept-Language``. Pydantic não preencher um valor default
    # aqui garante que ``body.locale`` seja ``None`` quando o cliente não
    # envia — útil para o fallback de Accept-Language.
    locale: str | None = Field(default=None, max_length=10)


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


def current_user(session: str | None, db: Session, locale: str | None = None, accept_language: str | None = None) -> User:
    payload = decode_token(session or "")
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_msg("session_required", locale, accept_language),
        )
    user = db.get(User, payload["user_id"])
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_msg("session_required", locale, accept_language),
        )
    # Reset de senha incrementou o epoch: tokens antigos viram inválidos.
    if payload["epoch"] != getattr(user, "token_epoch", 0):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_msg("session_required", locale, accept_language),
        )
    return user


def set_session(response: Response, user: User) -> None:
    epoch = int(getattr(user, "token_epoch", 0) or 0)
    response.set_cookie(
        "site_session",
        create_token(user.id, epoch),
        httponly=True,
        secure=os.getenv("COOKIE_SECURE", "1") == "1",
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )


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
def register(body: RegisterBody, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    email = body.email.lower()
    locale = _pick_locale(body.locale, request.headers.get("accept-language"))
    existing = db.scalar(select(User).where(User.email == email))
    # Hash sempre: equilibra o tempo de resposta entre o caminho novo e o de
    # duplicata. Sem isso, um atacante mede a latência e descobre que
    # ``hash_password`` só roda no caminho novo.
    hashed = hash_password(body.password)
    if existing:
        # Não revela que o e-mail já está cadastrado: responde como se fosse um
        # cadastro novo, sem emitir cookie de sessão. O dono real continua
        # podendo logar normalmente; a UI deve mostrar uma mensagem neutra
        # quando ``created`` é False.
        try:
            from .mailer import send_existing_account_notice
            send_existing_account_notice(existing.email, locale)
        except Exception:
            # Mailer ausente ou falhou: silencioso. Não vaza.
            pass
        return {"user": {"id": existing.id, "email": existing.email, "name": existing.name, "locale": existing.locale}, "created": False}
    user = User(email=email, password_hash=hashed, name=body.name, locale=locale)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # Corrida rara: alguém inseriu o mesmo e-mail entre o SELECT e o INSERT.
        # Mesmo tratamento: resposta neutra, sem cookie.
        db.rollback()
        existing = db.scalar(select(User).where(User.email == email))
        if existing:
            return {"user": {"id": existing.id, "email": existing.email, "name": existing.name, "locale": existing.locale}, "created": False}
        raise HTTPException(status_code=500, detail=_msg("validation_required", locale))
    db.refresh(user)
    set_session(response, user)
    return {"user": {"id": user.id, "email": user.email, "name": user.name, "locale": user.locale}, "created": True}


@app.post("/api/auth/login", dependencies=[Depends(auth_rate_limit)])
def login(body: LoginBody, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    locale = _pick_locale(getattr(body, "locale", None), request.headers.get("accept-language"))
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail=_msg("login_invalid", locale))
    set_session(response, user)
    return {"user": {"id": user.id, "email": user.email, "name": user.name, "locale": user.locale}}


@app.post("/api/auth/logout")
def logout(response: Response) -> dict:
    response.delete_cookie("site_session")
    return {"ok": True}


# --- Recuperação de senha ----------------------------------------------------
# Fluxo anti-enumeração:
# - /request aceita qualquer e-mail e devolve 200 com a mesma mensagem
#   localizada. Só dispara o e-mail de verdade se a conta existe.
# - O token (32 bytes URL-safe) vai no link do e-mail; o banco guarda
#   apenas SHA-256(token). TTL curto (30min); uso único.
# - /confirm consome o token: valida hash, expiração, uso; rotaciona a
#   senha; incrementa ``token_epoch`` invalidando todas as sessões.

RESET_TOKEN_TTL_SECONDS = int(os.getenv("PASSWORD_RESET_TTL_SECONDS", "1800"))


class PasswordResetRequestBody(BaseModel):
    email: EmailStr
    locale: str = Field(default="pt-BR", max_length=10)


class PasswordResetConfirmBody(BaseModel):
    token: str = Field(min_length=16, max_length=128)
    password: str = Field(min_length=8)
    locale: str = Field(default="pt-BR", max_length=10)


def _reset_portal_url() -> str:
    return os.getenv("PORTAL_URL", "https://dash.astrodicas.pnzdigital.com.br/").rstrip("/")


@app.post("/api/auth/password-reset/request", dependencies=[Depends(password_reset_rate_limit)])
def password_reset_request(body: PasswordResetRequestBody, request: Request, db: Session = Depends(get_db)) -> dict:
    locale = _pick_locale(body.locale, request.headers.get("accept-language"))
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user:
        # Igualamos a latência do caminho "conta existe" e "conta inexistente"
        # gerando e descartando um token por requisição. O custo do SHA-256 é
        # mínimo mas mantém o work factor simétrico.
        raw, hashed = new_reset_token()
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=RESET_TOKEN_TTL_SECONDS)
        db.add(PasswordResetToken(user_id=user.id, token_hash=hashed, expires_at=expires_at, locale=locale))
        db.commit()
        try:
            from .mailer import send_password_reset
            send_password_reset(user.email, raw, expires_at, locale, _reset_portal_url())
        except Exception:
            # Falha do mailer não vaza status; o cliente recebe a mesma
            # confirmação neutra.
            pass
    else:
        # Gera e descarta pra equalizar tempo.
        new_reset_token()
    # Resposta sempre 200, sempre com a mesma chave, na língua do cliente.
    return {"detail": _msg("reset_request_ok", locale)}


@app.post("/api/auth/password-reset/confirm")
def password_reset_confirm(body: PasswordResetConfirmBody, response: Response, db: Session = Depends(get_db)) -> dict:
    locale = _pick_locale(body.locale, None)
    hashed = hash_reset_token(body.token)
    # ``hmac.compare_digest`` evita timing attacks sobre a busca do hash.
    candidates = db.scalars(
        select(PasswordResetToken).where(PasswordResetToken.used == False)  # noqa: E712
    ).all()
    matched: PasswordResetToken | None = None
    for candidate in candidates:
        if hmac.compare_digest(candidate.token_hash.encode(), hashed.encode()):
            matched = candidate
            break
    now = datetime.now(timezone.utc)
    if not matched or matched.expires_at.replace(tzinfo=timezone.utc if matched.expires_at.tzinfo is None else matched.expires_at.tzinfo) < now:
        # Mesmo detalhe para token expirado, já usado ou adulterado.
        raise HTTPException(status_code=400, detail=_msg("reset_token_invalid", locale))
    user = db.get(User, matched.user_id)
    if not user:
        raise HTTPException(status_code=400, detail=_msg("reset_token_invalid", locale))
    user.password_hash = hash_password(body.password)
    # Invalida todas as sessões existentes deste usuário.
    user.token_epoch = int(getattr(user, "token_epoch", 0) or 0) + 1
    matched.used = True
    db.commit()
    # Opcionalmente já autenticamos o usuário após o reset; aqui optamos
    # por não emitir cookie — força um novo login (mais seguro contra
    # tokens vazados em histórico).
    return {"detail": _msg("reset_done", locale)}


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
    payload = decode_token(site_session or "")
    if not payload:
        return {"authenticated": False}
    user = db.get(User, payload["user_id"])
    if not user:
        return {"authenticated": False}
    # Sessão de epoch antigo (pré-reset) também não autentica.
    if payload["epoch"] != int(getattr(user, "token_epoch", 0) or 0):
        return {"authenticated": False}
    return {"authenticated": True, "user": {"id": user.id, "email": user.email, "name": user.name, "locale": user.locale}}


@app.get("/api/me/profile")
def get_profile(request: Request, site_session: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> dict:
    user = current_user(site_session, db, accept_language=request.headers.get("accept-language"))
    profile = db.get(Profile, user.id)
    return {"profile": profile_to_dict(profile) if profile else None}


@app.put("/api/me/profile")
def save_profile(body: ProfileBody, request: Request, site_session: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> dict:
    user = current_user(site_session, db, accept_language=request.headers.get("accept-language"))
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
def access(request: Request, site_session: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> dict:
    user = current_user(site_session, db, accept_language=request.headers.get("accept-language"))
    return {"entitlements": [{"product_id": e.product_id, "status": e.status} for e in user.entitlements]}


@app.get("/api/me/readings")
def readings(request: Request, site_session: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> dict:
    user = current_user(site_session, db, accept_language=request.headers.get("accept-language"))
    rows = db.scalars(select(Reading).where(Reading.user_id == user.id).order_by(Reading.created_at.desc())).all()
    return {"readings": [reading_to_dict(row) for row in rows]}


@app.post("/api/me/readings/{content_id}/generate")
def generate(content_id: str, request: Request, site_session: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> dict:
    user = current_user(site_session, db, accept_language=request.headers.get("accept-language"))
    profile = db.get(Profile, user.id)
    if not profile or not profile.birth_date or not profile.birth_city:
        locale = _pick_locale(user.locale if user else None, request.headers.get("accept-language"))
        msg = "Complete seus dados de nascimento antes de gerar a leitura." if locale == "pt-BR" else "Completá tus datos de nacimiento antes de generar la lectura."
        raise HTTPException(status_code=422, detail=msg)
    snapshot = profile_to_dict(profile)
    existing = db.scalar(select(Reading).where(Reading.user_id == user.id, Reading.content_id == content_id, Reading.status.in_(["ready", "fallback"])).order_by(Reading.created_at.desc()))
    if existing and reading_is_current(existing, content_id, snapshot):
        return {"reading": reading_to_dict(existing)}
    product_id = content_product(content_id)
    if product_id and not db.scalar(select(Entitlement).where(Entitlement.user_id == user.id, Entitlement.product_id == product_id, Entitlement.status == "available")):
        locale = _pick_locale(user.locale if user else None, request.headers.get("accept-language"))
        msg = "Este conteúdo ainda não está liberado para sua conta." if locale == "pt-BR" else "Este contenido todavía no está disponible para tu cuenta."
        raise HTTPException(status_code=403, detail=msg)
    reading = Reading(user_id=user.id, content_id=content_id, product_id=product_id, status="in_progress", title=content_title(content_id), input_snapshot=snapshot)
    db.add(reading)
    db.commit()
    generated = generate_reading(content_id, reading.title, profile, user.locale, user.name)
    reading.body_html = generated.body_html
    reading.source = generated.source
    if generated.source == "fallback":
        reading.error_message = generated.warning
        reading.status = "fallback"
    else:
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
    return {
        "id": reading.id,
        "content_id": reading.content_id,
        "product_id": reading.product_id,
        "status": reading.status,
        "title": reading.title,
        "body_html": reading.body_html,
        "source": getattr(reading, "source", "llm"),
        "warning": reading.error_message if reading.status == "fallback" else "",
        "created_at": reading.created_at.isoformat(),
        "updated_at": reading.updated_at.isoformat(),
    }


async def await_request_body(request: Request) -> bytes:
    return await request.body()
