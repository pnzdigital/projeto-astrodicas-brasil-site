import hashlib
import hmac
import json
import logging
import os
import secrets
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, Cookie, Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Base, SessionLocal, engine, get_db
from .astrology import resolve_coordinates
from .mailer import send_purchase_confirmation
from . import admin, checkout, entitlements, ggcheckout, horoscope_free, migrations, preview, pricing, renewal, security, sessions, subscriptions, worker as _worker
from .ratelimit import auth_rate_limit, password_reset_rate_limit, webhook_rate_limit
from .engine import generate_reading, sections_for
from .pdf_export import build_pdf
from .models import Entitlement, GenerationJob, Order, PasswordResetToken, Profile, Reading, User, WebhookEvent
from .security import (
    create_token,
    decode_token,
    hash_password,
    hash_reset_token,
    new_reset_token,
    verify_password,
)


logger = logging.getLogger(__name__)

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
app.include_router(ggcheckout.router)
app.include_router(admin.router)
app.include_router(preview.router)
app.include_router(horoscope_free.router)
app.include_router(subscriptions.router)
app.include_router(renewal.router)


@app.middleware("http")
async def refresh_session(request: Request, call_next):
    """Estende a sessão de quem está usando o site.

    A janela é de 7 dias contados da emissão, então sem isso um cliente ativo
    seria deslogado no meio do uso. Só reescrevemos o cookie depois que o token
    passou de ``REFRESH_AFTER_SECONDS``, e nunca em resposta de erro — assim um
    401 não devolve sessão nova.
    """
    response = await call_next(request)
    if response.status_code >= 400:
        return response
    payload = decode_token(request.cookies.get("site_session") or "")
    if payload and security.should_refresh(payload["issued_at"]):
        _write_session_cookie(response, payload["user_id"], payload["epoch"])
    return response


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
    birth_form = path.startswith("/api/preview/") or path.startswith("/api/horoscopo/")
    if not path.startswith("/api/auth/") and not birth_form:
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
    if birth_form:
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
    birth_state: str | None = Field(default=None, max_length=64)
    birth_country: str = Field(default="BR", min_length=2, max_length=2)
    birth_timezone: str = Field(default="America/Sao_Paulo", max_length=64)
    birth_latitude: str | None = Field(default=None, max_length=32)
    birth_longitude: str | None = Field(default=None, max_length=32)
    partner_name: str = Field(default="", max_length=160)
    partner_birth_date: date | None = None
    partner_birth_time: time | None = None
    partner_birth_city: str = Field(default="", max_length=160)
    partner_birth_state: str | None = Field(default=None, max_length=64)
    partner_country: str = Field(default="BR", min_length=2, max_length=2)

    @field_validator("birth_time", "partner_birth_time", mode="before")
    @classmethod
    def _blank_time_to_none(cls, value):
        return None if value == "" else value


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


def _write_session_cookie(response: Response, user_id: str, epoch: int) -> None:
    response.set_cookie(
        "site_session",
        create_token(user_id, epoch),
        httponly=True,
        secure=os.getenv("COOKIE_SECURE", "1") == "1",
        samesite="lax",
        max_age=security.SESSION_TTL_SECONDS,
    )


def set_session(response: Response, user: User) -> None:
    _write_session_cookie(response, user.id, sessions.current_epoch(user))


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
        for name in ("CAKTO_WEBHOOK_SECRET", "ADMIN_PASSWORD")
        if not os.getenv(name, "").strip()
    ]
    # O Mercado Pago tem uma rota por aplicação: basta a clave da aplicação
    # argentina para o fluxo em uso. Exigir as duas impediria o dia em que a
    # rota legada for aposentada e só MP_WEBHOOK_SECRET_AR sobrar configurada.
    if not (os.getenv("MP_WEBHOOK_SECRET", "").strip() or os.getenv("MP_WEBHOOK_SECRET_AR", "").strip()):
        missing.append("MP_WEBHOOK_SECRET_AR (ou MP_WEBHOOK_SECRET)")
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
    try:
        from sqlalchemy import func, select as sa_select
        db = SessionLocal()
        try:
            pending = db.scalar(
                sa_select(func.count()).where(
                    GenerationJob.status.in_(["queued", "running"])
                )
            ) or 0
            failed_recent = db.scalar(
                sa_select(func.count()).where(GenerationJob.status == "failed")
            ) or 0
        finally:
            db.close()
        queue_info: dict = {"pending": pending, "failed": failed_recent}
    except Exception:
        queue_info = {"pending": -1, "failed": -1}
    return {
        "ok": True,
        "service": "astrodicas-site",
        "channel": "site",
        "queue": queue_info,
    }


# /api/auth/register foi removido: a única forma de criar conta é a compra
# (webhook aprovado -> fulfill_order). O dash só expõe login. Rota inexistente
# devolve 404 nativo do FastAPI, sem handler dedicado.


@app.post("/api/auth/login", dependencies=[Depends(auth_rate_limit)])
def login(body: LoginBody, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    locale = _pick_locale(getattr(body, "locale", None), request.headers.get("accept-language"))
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail=_msg("login_invalid", locale))
    set_session(response, user)
    return {"user": {"id": user.id, "email": user.email, "name": user.name, "locale": user.locale}}


@app.post("/api/auth/logout")
def logout(
    response: Response,
    site_session: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """Apagar o cookie não basta: quem já copiou o token continuaria dentro.
    Incrementamos o epoch, então o token sai de circulação no servidor."""
    payload = decode_token(site_session or "")
    if payload:
        user = db.get(User, payload["user_id"])
        if user and payload["epoch"] == sessions.current_epoch(user):
            sessions.revoke_sessions(user)
            db.commit()
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
        coordinates = resolve_coordinates(profile.birth_city, profile.birth_country, profile.birth_state)
        if coordinates:
            profile.birth_latitude, profile.birth_longitude = map(str, coordinates)
    db.add(profile)
    db.commit()
    return {"profile": profile_to_dict(profile)}


@app.get("/api/me/access")
def access(request: Request, site_session: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> dict:
    user = current_user(site_session, db, accept_language=request.headers.get("accept-language"))
    now = datetime.now(timezone.utc)
    return {
        "entitlements": [
            _entitlement_dict(e, now)
            for e in user.entitlements
        ]
    }


def _entitlement_dict(e: "Entitlement", now: datetime) -> dict:
    """Serializa um entitlement com campos de conveniência para o painel."""
    is_active = entitlements.is_active(e, now)
    expires_at = e.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    days_remaining: int | None = None
    needs_renewal: bool | None = None
    if expires_at is not None:
        delta = expires_at - now
        days_remaining = max(0, delta.days)
        needs_renewal = not is_active

    return {
        "product_id": e.product_id,
        "status": e.status,
        "active": is_active,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "days_remaining": days_remaining,
        "needs_renewal": needs_renewal,
    }


@app.get("/api/me/readings")
def readings(request: Request, site_session: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> dict:
    user = current_user(site_session, db, accept_language=request.headers.get("accept-language"))
    rows = db.scalars(select(Reading).where(Reading.user_id == user.id).order_by(Reading.created_at.desc())).all()
    return {"readings": [reading_to_dict(row, user.locale) for row in rows]}


@app.get("/api/me/alerts")
def astro_alerts(request: Request, site_session: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> dict:
    """Eventos astronômicos de hoje visíveis no painel (só para assinantes pagas)."""
    user = current_user(site_session, db, accept_language=request.headers.get("accept-language"))
    if not entitlements.active_paid(db, user.id, "site:diario_astral"):
        return {"alerts": []}

    from .astrology import lunar_event_today, retrograde_starts_today

    today = datetime.now(timezone.utc).date()
    alerts = []
    event = lunar_event_today(today)
    if event:
        alerts.append({"type": event, "date": today.isoformat()})
    for planet in retrograde_starts_today(today):
        alerts.append({"type": "retrogrado", "planet": planet, "date": today.isoformat()})

    return {"alerts": alerts}


def _run_generation_job(reading_id: str, content_id: str, title: str, user_id: str, locale: str, customer_name: str) -> None:
    """Roda `generate_reading` fora do ciclo request/response.

    Corre num BackgroundTask do FastAPI/Starlette — mesmo worker, mas depois
    que a resposta 202 já foi enviada ao cliente, então a chamada ao MiniMax
    (segundos a poucos minutos, mesmo seccionada) nunca mais bloqueia o
    request HTTP nem estoura o proxy_read_timeout do nginx.

    Risco documentado (BackgroundTasks não é uma fila durável): se o
    container reiniciar enquanto esta task está rodando, a task morre com
    ele e a `Reading` fica presa em `status="in_progress"` para sempre — sem
    processo nenhum vai retomá-la sozinho. Mitigação hoje: `GET
    /api/me/readings` mostra `in_progress` no portal, então o cliente não vê
    silêncio; se ficar travado, gerar de novo (mesmo content_id) cria uma
    nova tentativa porque `reading_is_current` só aceita linhas com status
    `ready`/`fallback`. Não é resumo automático — é reentrada manual. Uma
    fila durável (ex.: tabela de jobs com `attempts`/`locked_at`, revisitada
    por um cron) resolveria isso de verdade, mas não foi adicionada aqui
    porque não há hoje um worker separado do processo web para consumi-la, e
    inventar um destes dois de uma vez só (fila OU worker) sem o outro não
    reduz o risco — fica registrado como dívida, não escondido.
    """
    db = SessionLocal()
    try:
        reading = db.get(Reading, reading_id)
        if not reading:
            logger.error("generate background job: reading %s sumiu antes de terminar", reading_id)
            return
        profile = db.get(Profile, user_id)
        expected = sections_for(content_id)
        # sections_total já foi setado em main.generate antes do 202; aqui
        # apenas resets sections_done para cobrir o caso improvável de retry.
        reading.sections_done = 0
        db.commit()

        done_count = [0]

        def _on_section_done() -> None:
            done_count[0] += 1
            reading.sections_done = done_count[0]
            db.commit()

        try:
            if expected:
                generated = generate_reading(content_id, title, profile, locale, customer_name, on_section_done=_on_section_done)
            else:
                generated = generate_reading(content_id, title, profile, locale, customer_name)
        except Exception:
            logger.exception("generate_reading levantou exceção não tratada para reading=%s", reading_id)
            reading.status = "fallback"
            reading.error_message = "Leitura gerada por modelo editorial padrão. A leitura personalizada está temporariamente indisponível."
            db.commit()
            return
        reading.body_html = generated.body_html
        reading.source = generated.source
        reading.content_sections = generated.sections or []
        reading.birth_time_assumed = generated.birth_time_assumed
        reading.ascendant_warning = generated.ascendant_warning or {}
        if generated.source == "fallback":
            reading.error_message = generated.warning
            reading.status = "fallback"
        else:
            reading.status = "ready"
        db.commit()
    finally:
        db.close()


@app.post("/api/me/readings/{content_id}/generate", status_code=status.HTTP_202_ACCEPTED)
def generate(content_id: str, request: Request, response: Response, background_tasks: BackgroundTasks, site_session: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> dict:
    user = current_user(site_session, db, accept_language=request.headers.get("accept-language"))
    profile = db.get(Profile, user.id)
    if not profile or not profile.birth_date or not profile.birth_city:
        locale = _pick_locale(user.locale if user else None, request.headers.get("accept-language"))
        msg = "Complete seus dados de nascimento antes de gerar a leitura." if locale == "pt-BR" else "Completá tus datos de nacimiento antes de generar la lectura."
        raise HTTPException(status_code=422, detail=msg)
    snapshot = profile_to_dict(profile)
    locale = _pick_locale(user.locale if user else None, request.headers.get("accept-language"))
    if content_id == "site:content:previsao_semanal":
        # Grava a semana-alvo no snapshot para que reading_is_current compare
        # pela semana-alvo (não pela semana de criação). Veja _target_iso_week.
        tz_locale = locale if locale in horoscope_free.LOCALE_DEFAULTS else "pt-BR"
        snapshot = {**(snapshot or {}), "target_iso_week": _target_iso_week(tz_locale)}
    product_id = content_product(content_id)
    # Entitlement checado ANTES do cache: sem isso, quem cancelou/venceu ainda
    # veria a leitura de hoje se ela já tivesse sido gerada antes de expirar.
    # Conteúdos PAID_ONLY_CONTENT exigem acesso pago — trial não abre.
    if product_id:
        checker = entitlements.active_paid if content_id in PAID_ONLY_CONTENT else entitlements.active
        if not checker(db, user.id, product_id):
            msg = "Este conteúdo ainda não está liberado para sua conta." if locale == "pt-BR" else "Este contenido todavía no está disponible para tu cuenta."
            raise HTTPException(status_code=403, detail=msg)
    existing = db.scalar(select(Reading).where(Reading.user_id == user.id, Reading.content_id == content_id, Reading.status.in_(["ready", "fallback"])).order_by(Reading.created_at.desc()))
    if existing and existing.status == "ready" and reading_is_current(existing, content_id, snapshot, locale):
        # Já pronta: nada para gerar, devolve 200 de verdade (não 202).
        response.status_code = status.HTTP_200_OK
        return {"reading": reading_to_dict(existing, user.locale)}
    # Fallback não é leitura pronta — tenta de novo. Limite: 3 por 24h para
    # evitar loop de regeneração cara se o LLM continuar falhando.
    if existing and existing.status == "fallback":
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        recent = db.scalars(
            select(Reading)
            .where(
                Reading.user_id == user.id,
                Reading.content_id == content_id,
                Reading.status == "fallback",
                Reading.created_at > cutoff,
            )
            .limit(3)
        ).all()
        if len(recent) >= 3:
            response.status_code = status.HTTP_200_OK
            return {"reading": reading_to_dict(existing, user.locale)}
    reading = Reading(
        user_id=user.id,
        content_id=content_id,
        product_id=product_id,
        status="in_progress",
        title=content_title(content_id),
        input_snapshot=snapshot,
        sections_total=len(sections_for(content_id)),  # exposto no 202 para o front
    )
    db.add(reading)
    db.commit()
    db.refresh(reading)
    response.status_code = status.HTTP_202_ACCEPTED
    # A resposta volta AGORA, com status "in_progress". A geração roda no
    # worker separado (ver worker.py) que consome a tabela generation_jobs.
    # O portal já faz polling em /api/me/readings até `ready`/`fallback`.
    _worker.enqueue_generation_job(reading.id, content_id, user.id, locale, user.name)
    return {"reading": reading_to_dict(reading, user.locale)}


@app.get("/api/me/readings/{content_id}/pdf")
def reading_pdf(content_id: str, request: Request, site_session: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> Response:
    user = current_user(site_session, db, accept_language=request.headers.get("accept-language"))
    reading = db.scalar(
        select(Reading)
        .where(Reading.user_id == user.id, Reading.content_id == content_id, Reading.status.in_(["ready", "fallback"]))
        .order_by(Reading.created_at.desc())
    )
    if not reading:
        locale = _pick_locale(user.locale if user else None, request.headers.get("accept-language"))
        msg = "Gere a leitura antes de baixar o PDF." if locale == "pt-BR" else "Genera la lectura antes de descargar el PDF."
        raise HTTPException(status_code=404, detail=msg)
    sections = reading.content_sections or []
    if not sections:
        locale = _pick_locale(user.locale if user else None, request.headers.get("accept-language"))
        msg = "PDF disponível apenas para leituras seccionadas." if locale == "pt-BR" else "PDF disponible solo para lecturas seccionadas."
        raise HTTPException(status_code=422, detail=msg)
    locale = _pick_locale(user.locale if user else None, request.headers.get("accept-language"))
    ascendant_warning = getattr(reading, "ascendant_warning", None) or {}
    ascendant_text = ascendant_warning.get(locale) or ascendant_warning.get("pt-BR") or ""
    fallback_text = reading.error_message if reading.status == "fallback" else ""
    # PDF baixado precisa carregar o mesmo aviso da tela — cliente guarda o
    # arquivo, então a ressalva de Ascendente estimado não pode ficar só na UI.
    warning = "\n\n".join(text for text in (fallback_text, ascendant_text) if text)
    pdf_bytes = build_pdf(reading.title, sections, customer_name=user.name, warning=warning)
    filename = f"{content_id.split(':')[-1]}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
    temp_password: str | None = None
    if not user:
        # A senha precisa sair daqui para o e-mail. Antes ela era descartada no
        # mesmo instante em que nascia (``os.urandom`` direto no hash), e o
        # cliente pagava, ganhava a conta e não tinha como entrar nela: sobrava
        # o "esqueci a senha", que depende do mesmo e-mail que ninguém mandou.
        temp_password = secrets.token_urlsafe(9)
        user = User(email=body.email.lower(), password_hash=hash_password(temp_password), name="Cliente AstroDicas")
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
            # Pagamento real promove trial → pago: garante que active_paid() passe.
            if entitlement.source == "trial":
                entitlement.source = "site"
    db.commit()

    # Depois do commit de propósito: o acesso é o que a pessoa pagou, e ele não
    # pode depender do provedor de e-mail estar de pé. Se o envio falhar, a
    # compra continua válida e o log grita — silêncio aqui significa cliente
    # pagante trancado do lado de fora, que é o pior jeito de descobrir.
    delivery = send_purchase_confirmation(
        email=user.email,
        name=user.name,
        product_title=pricing.title_for(body.product_id, None),
        amount_label=pricing.format_amount(body.amount_minor, body.currency),
        temp_password=temp_password,
    )
    if not delivery.get("sent"):
        logger.error(
            "Compra confirmada sem e-mail entregue: user=%s product=%s erro=%s",
            user.id,
            body.product_id,
            delivery.get("error"),
        )
    return {"ok": True, "user_id": user.id, "product_id": body.product_id}


# Conteúdos que exigem acesso PAGO (source != "trial") ao diário astral.
# Trial libera apenas o horóscopo diário. Guia do Mês e Previsão Semanal
# pertencem ao Diário Astral pago — não ao Mapa Astral avulso (que dá
# site:mapa_astral, não site:diario_astral) e não ao trial.
PAID_ONLY_CONTENT: frozenset[str] = frozenset({
    "site:content:guia_do_mes",
    "site:content:previsao_semanal",
})


def content_product(content_id: str) -> str | None:
    return {
        # trial + pago: horóscopo diário desbloqueado durante os 3 dias grátis
        "site:content:horoscopo_diario": "site:diario_astral",
        # Guia do Mês e Previsão Semanal pertencem ao Diário Astral pago.
        # Exigem active_paid (ver PAID_ONLY_CONTENT): trial não abre, Mapa
        # Astral avulso (site:mapa_astral) não abre — só quem pagou o Diário.
        "site:content:guia_do_mes": "site:diario_astral",
        "site:content:mapa_astral_completo": "site:mapa_astral",
        "site:content:previsao_semanal": "site:diario_astral",
        # Círculo Completo (pagamento único / oferta premium)
        "site:content:mapa_do_amor_sinastria": "site:mapa_amor_sinastria",
        "site:content:mapa_da_carreira": "site:mapa_carreira",
        "site:content:mapa_da_prosperidade": "site:mapa_prosperidade",
        "site:content:calendario_lunar": "site:diario_astral_completo",
        "site:content:guia_dos_retrogrados": "site:diario_astral_completo",
        "site:content:manual_do_ascendente": "site:diario_astral_completo",
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


def _target_iso_week(tz_locale: str) -> str:
    """Semana-alvo da previsão semanal no fuso local da assinante.

    Do sábado em diante → a semana que começa na segunda seguinte;
    antes do sábado → a semana corrente.  Grava como "YYYY-Www" para
    comparação explícita no input_snapshot (evita ambiguidade na fronteira
    do sábado, onde criação de sexta e de sábado apontam para semanas
    diferentes).
    """
    today = horoscope_free.local_today(tz_locale)
    if today.weekday() >= 5:  # Saturday=5, Sunday=6 → next week
        next_monday = today + timedelta(days=7 - today.weekday())
        year, week, _ = next_monday.isocalendar()
    else:
        year, week, _ = today.isocalendar()
    return f"{year}-W{week:02d}"


def reading_is_current(reading: Reading, content_id: str, snapshot: dict, locale: str = "pt-BR") -> bool:
    if reading.input_snapshot != snapshot:
        return False
    created = reading.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if content_id == "site:content:horoscopo_diario":
        # Dia da ASSINANTE, não do servidor — mesma razão do horóscopo grátis
        # (horoscope_free.local_today): às 22h no Brasil/Argentina o servidor
        # (UTC) já virou o dia seguinte, e contar por UTC serviria o horóscopo
        # de amanhã de noite e recusaria cache válido de hoje de manhã.
        tz_locale = locale if locale in horoscope_free.LOCALE_DEFAULTS else "pt-BR"
        created_local = created.astimezone(ZoneInfo(horoscope_free.LOCALE_DEFAULTS[tz_locale][1])).date()
        return created_local == horoscope_free.local_today(tz_locale)
    if content_id == "site:content:previsao_semanal":
        # Chave de validade = semana-alvo gravada no snapshot (não a semana de
        # criação): a semana-alvo muda no sábado, não na segunda.  Se o snapshot
        # passou a comparação acima, target_iso_week já bate — verificamos
        # explicitamente para deixar a intenção clara e proteger testes diretos.
        tz_locale = locale if locale in horoscope_free.LOCALE_DEFAULTS else "pt-BR"
        stored_week = (reading.input_snapshot or {}).get("target_iso_week")
        return bool(stored_week) and stored_week == _target_iso_week(tz_locale)
    if content_id == "site:content:guia_do_mes":
        # Mês da ASSINANTE, não do servidor — mesma razão do horóscopo diário
        # acima: perto da virada do mês, UTC já mudou de mês enquanto ainda é
        # o mês anterior na Argentina/Brasil (ou o inverso), e isso decidiria
        # errado se regenera o guia ou serve o cache.
        tz_locale = locale if locale in horoscope_free.LOCALE_DEFAULTS else "pt-BR"
        created_local = created.astimezone(ZoneInfo(horoscope_free.LOCALE_DEFAULTS[tz_locale][1])).date()
        today_local = horoscope_free.local_today(tz_locale)
        return (created_local.year, created_local.month) == (today_local.year, today_local.month)
    if content_id == "site:content:calendario_lunar":
        # Mês da ASSINANTE, não do servidor — à meia-noite em UTC o servidor já
        # virou o mês enquanto Brasil/Argentina ainda estão no anterior.
        tz_locale = locale if locale in horoscope_free.LOCALE_DEFAULTS else "pt-BR"
        created_local = created.astimezone(ZoneInfo(horoscope_free.LOCALE_DEFAULTS[tz_locale][1])).date()
        today_local = horoscope_free.local_today(tz_locale)
        return (created_local.year, created_local.month) == (today_local.year, today_local.month)
    return True


def profile_to_dict(profile: Profile | None) -> dict | None:
    if not profile:
        return None
    return {key: getattr(profile, key).isoformat() if isinstance(getattr(profile, key), (date, time)) else getattr(profile, key) for key in ("user_id", "birth_date", "birth_time", "birth_city", "birth_state", "birth_country", "birth_timezone", "birth_latitude", "birth_longitude", "partner_name", "partner_birth_date", "partner_birth_time", "partner_birth_city", "partner_birth_state", "partner_country")}


def reading_to_dict(reading: Reading, locale: str = "pt-BR") -> dict:
    ascendant_warning = getattr(reading, "ascendant_warning", None) or {}
    return {
        "id": reading.id,
        "content_id": reading.content_id,
        "product_id": reading.product_id,
        "status": reading.status,
        "title": reading.title,
        "body_html": reading.body_html,
        "source": getattr(reading, "source", "llm"),
        "sections": getattr(reading, "content_sections", None) or [],
        "warning": reading.error_message if reading.status == "fallback" else "",
        # Espelha ReadingResult.birth_time_assumed/ascendant_warning: sem isso
        # o cliente pagante vê o Ascendente calculado como se fosse exato.
        "birth_time_assumed": bool(getattr(reading, "birth_time_assumed", False)),
        "ascendant_warning": ascendant_warning.get(locale) or ascendant_warning.get("pt-BR") or "",
        "sections_done": int(getattr(reading, "sections_done", 0) or 0),
        "sections_total": int(getattr(reading, "sections_total", 0) or 0),
        "created_at": reading.created_at.isoformat(),
        "updated_at": reading.updated_at.isoformat(),
    }


async def await_request_body(request: Request) -> bytes:
    return await request.body()
