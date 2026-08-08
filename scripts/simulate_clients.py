#!/usr/bin/env python3
"""
scripts/simulate_clients.py
Simulação de clientes reais — 8 cenários de carga, race condition e i18n.

Banco isolado: data/sim.db (nunca toca dev.db nem produção).
MiniMax: stub com latência simulada (3-8s/seção). Chamadas reais: no máximo 2.
Uso: cd astrodicas-site && .venv/bin/python scripts/simulate_clients.py
"""
from __future__ import annotations

import os
import sys
import time
import json
import random
import uuid
import threading
import statistics
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIM_DB = ROOT / "data" / "sim.db"
REPORT_PATH = Path("/tmp/claude-1000/simulacao-clientes.md")

# ── 1. Ambiente ────────────────────────────────────────────────────────────────

os.environ["DATABASE_URL"] = f"sqlite:///{SIM_DB}"
os.environ["SITE_SECRET_KEY"] = "sim-only-secret-at-least-32-bytes-xxxxxxxxxxxxxxxxxx"
os.environ["COOKIE_SECURE"] = "0"
os.environ["SITE_ORIGIN"] = "http://test"
os.environ["GEOCODING_ENABLED"] = "1"
os.environ["ENV"] = "test"
os.environ["ALLOW_INSECURE_DEV"] = "1"
os.environ["RATE_LIMIT_ENABLED"] = "0"
os.environ["WORKER_POLL_INTERVAL_SECONDS"] = "0.5"
os.environ["MINIMAX_MAX_CONCURRENCY"] = "8"
os.environ["JOB_VISIBILITY_TIMEOUT_MINUTES"] = "1"
os.environ["MINIMAX_SECTION_MAX_ATTEMPTS"] = "2"
os.environ["MINIMAX_SECTION_TIMEOUT_SECONDS"] = "20"
os.environ["MINIMAX_SECTION_POOL_SIZE"] = "4"
os.environ["MINIMAX_API_KEY"] = "sim-fake-key"  # non-empty → MiniMax path is taken
# GG Checkout: produto simulado → produto interno. Sem secret → ALLOW_INSECURE_DEV passa.
os.environ["GG_PRODUCT_MAP"] = '{"gg-sim-product": "site:diario_astral"}'

sys.path.insert(0, str(ROOT / "api"))

# ── 2. MiniMax stub (antes de qualquer import do engine) ───────────────────────

import app.engine as _engine  # noqa: E402

_real_call_count = 0
_real_call_lock = threading.Lock()
_MAX_REAL_CALLS = 2

_STUB_PT = (
    "## {title}\n"
    "### {subtitle}\n\n"
    "Este é um parágrafo de simulação em português brasileiro com conteúdo "
    "suficiente para o sistema de geração de leituras astrológicas. "
    "As posições planetárias revelam um caminho de autoconhecimento.\n\n"
    "Segundo parágrafo da seção simulada com texto sobre o tema astrológico. "
    "Os trânsitos do momento iluminam aspectos importantes da trajetória pessoal.\n\n"
    "Terceiro parágrafo com reflexão prática. O caminho se revela com clareza "
    "ao observar os movimentos celestes com atenção e abertura."
)

_STUB_ES = (
    "## {title}\n"
    "### {subtitle}\n\n"
    "Este es un párrafo de simulación en español rioplatense con contenido "
    "suficiente para el sistema de generación de lecturas astrológicas. "
    "Las posiciones planetarias revelan un camino de autoconocimiento.\n\n"
    "Segundo párrafo de la sección simulada con texto sobre el tema astrológico. "
    "Los tránsitos del momento iluminan aspectos importantes de la trayectoria personal.\n\n"
    "Tercer párrafo con reflexión práctica. El camino se revela con claridad "
    "al observar los movimientos celestes con atención y apertura."
)

_orig_call = _engine._call_minimax


def _stub_minimax(prompt, locale="pt-BR", max_tokens=None, timeout=None):
    global _real_call_count
    do_real = False
    with _real_call_lock:
        if _real_call_count < _MAX_REAL_CALLS:
            _real_call_count += 1
            do_real = True
            n = _real_call_count

    if do_real:
        print(f"  [MiniMax REAL #{n}] locale={locale} max_tokens={max_tokens}", flush=True)
        try:
            return _orig_call(prompt, locale, max_tokens, timeout)
        except Exception as exc:
            print(f"  [MiniMax REAL #{n}] FALHOU: {exc}", flush=True)
            raise

    # Latência simulada igual à produção
    time.sleep(random.uniform(3, 8))

    # Extrai título/subtítulo do prompt de seção
    import re
    t = re.search(r"Título desta seção: (.+?)(?:\n|$)", prompt)
    s = re.search(r"Subtítulo desta seção: (.+?)(?:\n|$)", prompt)
    title = (t.group(1) if t else "Seção Simulada").strip()
    subtitle = (s.group(1) if s else "Subtítulo").strip()

    template = _STUB_ES if locale == "es-AR" else _STUB_PT
    return template.format(title=title, subtitle=subtitle)


_engine._call_minimax = _stub_minimax

# ── 3. Imports do app ──────────────────────────────────────────────────────────

import httpx  # noqa: E402

from app.db import Base, SessionLocal, engine as db_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Entitlement, GenerationJob, Order, Reading, Subscription, User, WebhookEvent,
)
from app.security import hash_password  # noqa: E402
from sqlalchemy import select  # noqa: E402
import app.worker as _worker  # noqa: E402
import app.ratelimit as _ratelimit  # noqa: E402
import app.migrations as _migrations  # noqa: E402

# ── 4. Banco isolado ───────────────────────────────────────────────────────────

if SIM_DB.exists():
    SIM_DB.unlink()
(ROOT / "data").mkdir(exist_ok=True)
Base.metadata.create_all(db_engine)
_migrations.ensure_schema()
_ratelimit.reset_all()
print("✓ sim.db criado", flush=True)

# ── 5. Worker em thread background ─────────────────────────────────────────────

_worker_thread: threading.Thread | None = None


def _start_worker() -> threading.Thread:
    global _worker_thread
    _worker._stop_event.clear()

    def _loop():
        import logging
        logging.basicConfig(level=logging.WARNING)
        db = SessionLocal()
        db.close()
        active: list[threading.Thread] = []
        while not _worker._stop_event.is_set():
            active = [t for t in active if t.is_alive()]
            if len(active) < _worker.MAX_CONCURRENCY:
                db = SessionLocal()
                try:
                    job = _worker._claim_next_job(db)
                    jid = job.id if job else None  # lê antes de close — expire_on_commit
                finally:
                    db.close()
                if jid is not None:
                    t = threading.Thread(target=_worker.run_job, args=(jid,), daemon=True)
                    t.start()
                    active.append(t)
                    continue
            _worker._stop_event.wait(timeout=_worker.POLL_INTERVAL_SECONDS)
        for t in active:
            t.join(timeout=120)

    t = threading.Thread(target=_loop, daemon=True, name="sim-worker")
    t.start()
    _worker_thread = t
    return t


def _stop_worker():
    _worker._stop_event.set()
    if _worker_thread:
        _worker_thread.join(timeout=10)


# ── 6. Helpers DB ─────────────────────────────────────────────────────────────


def _create_user_in_db(email: str, password: str, name: str = "Cliente Sim",
                        locale: str = "pt-BR") -> User:
    db = SessionLocal()
    try:
        existing = db.scalar(select(User).where(User.email == email))
        if existing:
            return existing
        user = User(email=email.lower(), password_hash=hash_password(password),
                    name=name, locale=locale)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def _grant_entitlement(user_id: str, product_id: str):
    db = SessionLocal()
    try:
        ent = db.scalar(select(Entitlement).where(
            Entitlement.user_id == user_id, Entitlement.product_id == product_id))
        if not ent:
            db.add(Entitlement(user_id=user_id, product_id=product_id,
                               status="available", source="sim"))
            db.commit()
    finally:
        db.close()


def _set_profile(user_id: str, city: str = "São Paulo", state: str | None = None,
                 country: str = "BR", birth_date: str = "1990-03-15",
                 birth_time: str | None = None, timezone_name: str = "America/Sao_Paulo"):
    from app.models import Profile
    db = SessionLocal()
    try:
        from datetime import date, time
        profile = db.get(Profile, user_id) or Profile(user_id=user_id)
        profile.birth_city = city
        profile.birth_state = state
        profile.birth_country = country
        profile.birth_date = date.fromisoformat(birth_date)
        profile.birth_time = time.fromisoformat(birth_time) if birth_time else None
        profile.birth_timezone = timezone_name
        db.add(profile)
        db.commit()
    finally:
        db.close()


def _count_db(model, **filters):
    db = SessionLocal()
    try:
        q = select(model)
        for attr, val in filters.items():
            q = q.where(getattr(model, attr) == val)
        return len(db.scalars(q).all())
    finally:
        db.close()


def _fetch_db(model, **filters):
    db = SessionLocal()
    try:
        q = select(model)
        for attr, val in filters.items():
            q = q.where(getattr(model, attr) == val)
        return db.scalars(q).all()
    finally:
        db.close()


# ── 7. Helpers HTTP ────────────────────────────────────────────────────────────


def _new_transport():
    return httpx.ASGITransport(app=app, raise_app_exceptions=False)


async def _login(client: httpx.AsyncClient, email: str, password: str) -> str | None:
    resp = await client.post("/api/auth/login", json={"email": email, "password": password})
    if resp.status_code == 200:
        return resp.cookies.get("site_session")
    return None


def _auth_headers(token: str) -> dict:
    return {"Cookie": f"site_session={token}"}


def _percentile(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = (p / 100) * (len(sorted_data) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(sorted_data) - 1)
    frac = idx - lo
    return sorted_data[lo] + frac * (sorted_data[hi] - sorted_data[lo])


# ── 8. Resultados ─────────────────────────────────────────────────────────────


@dataclass
class ScenarioResult:
    name: str
    description: str
    successes: int = 0
    failures: int = 0
    errors: dict = field(default_factory=dict)
    latencies: list[float] = field(default_factory=list)
    defects: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def total(self):
        return self.successes + self.failures

    @property
    def p50(self):
        return round(_percentile(self.latencies, 50), 2)

    @property
    def p95(self):
        return round(_percentile(self.latencies, 95), 2)


# ── 9. Cenários ───────────────────────────────────────────────────────────────

CITIES_BR = [
    "São Paulo", "Rio de Janeiro", "Belo Horizonte", "Salvador", "Fortaleza",
    "Curitiba", "Manaus", "Recife", "Porto Alegre", "Belém",
    "Goiânia", "Florianópolis", "Maceió", "Natal", "Teresina",
]

CITIES_AR = [
    "Buenos Aires", "Córdoba", "Rosario", "Mendoza", "La Plata",
    "San Miguel de Tucumán", "Mar del Plata", "Salta", "Santa Fe", "San Juan",
]

HOROSCOPE_PAYLOAD_BR = lambda i: {  # noqa: E731
    "name": f"Cliente{i:03d}",
    "birth_date": f"199{i % 10}-0{1 + i % 9}-{10 + i % 20:02d}",
    "birth_city": CITIES_BR[i % len(CITIES_BR)],
    "birth_country": "BR",
    "locale": "pt-BR",
}

HOROSCOPE_PAYLOAD_AR = lambda i: {  # noqa: E731
    "name": f"Cliente{i:03d}",
    "birth_date": f"199{i % 10}-0{1 + i % 9}-{10 + i % 20:02d}",
    "birth_city": CITIES_AR[i % len(CITIES_AR)],
    "birth_country": "AR",
    "locale": "es-AR",
}


async def scenario_1_free_horoscope(
    client: httpx.AsyncClient, n: int = 30, locale: str = "pt-BR"
) -> ScenarioResult:
    """C1/C8a: N clientes simultâneos no horóscopo grátis."""
    label = "C1" if locale == "pt-BR" else "C8a"
    result = ScenarioResult(
        name=f"{label} – {n} horóscopo grátis simultâneos ({locale})",
        description=f"POST /api/horoscopo/gratis × {n} concorrentes",
    )
    payload_fn = HOROSCOPE_PAYLOAD_BR if locale == "pt-BR" else HOROSCOPE_PAYLOAD_AR

    async def one(i):
        t0 = time.perf_counter()
        resp = await client.post("/api/horoscopo/gratis", json=payload_fn(i))
        return resp.status_code, time.perf_counter() - t0, resp

    tasks = [one(i) for i in range(n)]
    responses = await asyncio.gather(*tasks)

    for status, elapsed, resp in responses:
        result.latencies.append(elapsed)
        if status == 200:
            result.successes += 1
        else:
            result.failures += 1
            result.errors[status] = result.errors.get(status, 0) + 1
            if status >= 500:
                result.defects.append(f"500 em horóscopo grátis: {resp.text[:120]}")

    if result.failures > 0:
        result.defects.append(
            f"{result.failures}/{n} requisições falharam (ver errors)"
        )
    return result


async def scenario_2_trials(
    client: httpx.AsyncClient, n: int = 20, locale: str = "pt-BR"
) -> ScenarioResult:
    """C2/C8b: N cadastros de trial simultâneos, incluindo e-mails duplicados."""
    label = "C2" if locale == "pt-BR" else "C8b"
    result = ScenarioResult(
        name=f"{label} – {n} trials simultâneos ({locale})",
        description="POST /api/trial/start × N concorrentes + 4 pares de e-mail duplicado",
    )

    # 16 e-mails únicos + 4 pares duplicados (mesmo e-mail enviado 2x ao mesmo tempo)
    unique_emails = [f"trial-{locale.lower()}-{uuid.uuid4().hex[:8]}@sim.example.com"
                     for _ in range(n - 4)]
    dup_emails = [f"trial-dup-{locale.lower()}-{uuid.uuid4().hex[:6]}@sim.example.com"
                  for _ in range(4)]
    emails = unique_emails + [e for pair in zip(dup_emails, dup_emails) for e in pair]
    random.shuffle(emails)

    trial_locale = "es-AR" if locale == "es-AR" else "pt-BR"

    async def one(email, idx):
        t0 = time.perf_counter()
        resp = await client.post("/api/trial/start", json={
            "email": email, "name": f"Cliente{idx}", "locale": trial_locale,
        })
        return email, resp.status_code, time.perf_counter() - t0, resp

    tasks = [one(e, i) for i, e in enumerate(emails)]
    responses = await asyncio.gather(*tasks)

    dup_statuses: dict[str, list[int]] = {}
    for email, status, elapsed, resp in responses:
        result.latencies.append(elapsed)
        if status in (200, 201):
            result.successes += 1
        elif status == 409:
            # Esperado para o segundo de cada par duplicado
            result.successes += 1
            result.notes.append(f"409 esperado para e-mail duplicado {email[:30]}")
        else:
            result.failures += 1
            result.errors[status] = result.errors.get(status, 0) + 1
            if status >= 500:
                result.defects.append(
                    f"GRAVE: 500 em /trial/start para {email}: {resp.text[:120]}"
                )

        if email in dup_emails:
            dup_statuses.setdefault(email, []).append(status)

    # Verifica: cada par duplicado deve ter um sucesso e um 409, nunca dois 200
    for email, statuses in dup_statuses.items():
        if statuses.count(200) > 1 or statuses.count(201) > 1:
            result.defects.append(
                f"GRAVE race condition: e-mail {email} ganhou dois trials simultâneos! "
                f"status={statuses}"
            )
        elif all(s >= 500 for s in statuses):
            result.defects.append(
                f"GRAVE: ambas requisições falharam com 5xx para {email}: {statuses}"
            )

    # Verifica DB: não deve haver duas subscriptions para o mesmo e-mail
    db = SessionLocal()
    try:
        for email in dup_emails:
            user = db.scalar(select(User).where(User.email == email))
            if user:
                subs = db.scalars(
                    select(Subscription).where(Subscription.user_id == user.id)
                ).all()
                if len(subs) > 1:
                    result.defects.append(
                        f"GRAVE: {len(subs)} subscriptions para {email} – race criou duplicata"
                    )
    finally:
        db.close()

    return result


def _make_webhook_payload(locale: str, payment_id: str, email: str) -> tuple[str, dict]:
    """Retorna (url, payload) para o webhook de compra de acordo com o locale."""
    if locale == "pt-BR":
        # Brasil: GG Checkout — autenticação por bearer, payload aninhado.
        # Com GG_CHECKOUT_SECRET vazio + ALLOW_INSECURE_DEV=1 passa sem header.
        url = "/api/webhooks/ggcheckout"
        payload = {
            "webhook": {"id": f"wh-{payment_id}"},
            "payment": {"id": payment_id, "status": "paid"},
            "customer": {"email": email, "name": "Cliente Sim"},
            "product": {"id": "gg-sim-product"},
            "products": [{"id": "gg-sim-product"}],
        }
    else:
        # Argentina: webhook legado mercadopago (WebhookBody direto, sem chamada MP API).
        url = "/api/webhooks/mercadopago"
        payload = {
            "event_id": payment_id,
            "email": email,
            "product_id": "site:diario_astral",
            "status": "paid",
            "amount_minor": 2990,
            "currency": "ARS",
        }
    return url, payload


async def scenario_3_webhooks(
    client: httpx.AsyncClient, n: int = 10, locale: str = "pt-BR"
) -> ScenarioResult:
    """C3/C8c: N webhooks de compra simultâneos."""
    label = "C3" if locale == "pt-BR" else "C8c"
    endpoint = "GG Checkout" if locale == "pt-BR" else "MP legacy"
    result = ScenarioResult(
        name=f"{label} – {n} webhooks de compra simultâneos ({locale})",
        description=f"POST {endpoint} × N concorrentes",
    )

    # 7 e-mails únicos + 3 pares (mesmo e-mail, evento diferente → simula compra duplicada de conta)
    unique_emails = [f"wh-{locale.lower().replace('-', '')}-{uuid.uuid4().hex[:8]}@sim.example.com"
                     for _ in range(n - 3)]
    shared_emails = [f"wh-shared-{locale.lower().replace('-', '')}-{uuid.uuid4().hex[:6]}@sim.example.com"
                     for _ in range(3)]

    events = []
    for email in unique_emails:
        pid = f"pay-{uuid.uuid4().hex[:12]}"
        url, payload = _make_webhook_payload(locale, pid, email)
        events.append((url, payload, email))
    for email in shared_emails:
        # Mesmo e-mail, dois payment IDs diferentes (compra em dois devices)
        for _ in range(2):
            pid = f"pay-{uuid.uuid4().hex[:12]}"
            url, payload = _make_webhook_payload(locale, pid, email)
            events.append((url, payload, email))

    random.shuffle(events)

    async def one(url, payload, email):
        t0 = time.perf_counter()
        resp = await client.post(url, json=payload)
        return resp.status_code, time.perf_counter() - t0, resp, email

    tasks = [one(url, payload, email) for url, payload, email in events]
    responses = await asyncio.gather(*tasks)

    for status, elapsed, resp, _email in responses:
        result.latencies.append(elapsed)
        if status == 200:
            result.successes += 1
        else:
            result.failures += 1
            result.errors[status] = result.errors.get(status, 0) + 1
            if status >= 500:
                result.defects.append(f"GRAVE: {status} em webhook: {resp.text[:120]}")
            elif status != 200:
                result.defects.append(f"GRAVE: webhook retornou {status}: {resp.text[:120]}")

    # Verifica: todo e-mail deve ter exatamente 1 user, 1+ entitlements
    db = SessionLocal()
    try:
        all_emails = set(unique_emails) | set(shared_emails)
        for email in all_emails:
            users = db.scalars(select(User).where(User.email == email)).all()
            if len(users) > 1:
                result.defects.append(
                    f"GRAVE: {len(users)} usuários para {email} – race criou duplicata de conta"
                )
            elif len(users) == 1:
                ents = db.scalars(
                    select(Entitlement).where(Entitlement.user_id == users[0].id)
                ).all()
                if not ents:
                    result.defects.append(
                        f"GRAVE: compra confirmada mas sem entitlement para {email}"
                    )
    finally:
        db.close()

    return result


async def scenario_4_concurrent_generation(
    client: httpx.AsyncClient, n: int = 15
) -> ScenarioResult:
    """C4: N clientes gerando Mapa Astral ao mesmo tempo. Servidor deve responder durante."""
    result = ScenarioResult(
        name=f"C4 – {n} gerações de Mapa Astral simultâneas",
        description="POST /generate × N, worker processa, server deve responder durante",
    )

    content_id = "site:content:mapa_astral_completo"
    product_id = "site:mapa_astral"
    cities = CITIES_BR
    users_tokens = []

    # Cria N usuários com entitlement e perfil
    for i in range(n):
        email = f"gen-{uuid.uuid4().hex[:8]}@sim.example.com"
        pwd = "senha12345"
        u = _create_user_in_db(email, pwd, f"Geradora{i}", "pt-BR")
        _grant_entitlement(u.id, product_id)
        _set_profile(u.id, city=cities[i % len(cities)],
                     birth_date=f"199{i % 10}-03-15", birth_time="14:30:00")
        token = await _login(client, email, pwd)
        if token:
            users_tokens.append((u.id, token))

    if not users_tokens:
        result.defects.append("Nenhum usuário conseguiu autenticar")
        return result

    # Envia todas as requisições /generate simultaneamente
    async def one_generate(user_id, token):
        t0 = time.perf_counter()
        resp = await client.post(
            f"/api/me/readings/{content_id}/generate",
            headers=_auth_headers(token),
        )
        return user_id, resp.status_code, time.perf_counter() - t0

    tasks = [one_generate(uid, tok) for uid, tok in users_tokens]
    gen_responses = await asyncio.gather(*tasks)

    for user_id, status, elapsed in gen_responses:
        result.latencies.append(elapsed)
        if status in (200, 202):
            result.successes += 1
        else:
            result.failures += 1
            result.errors[status] = result.errors.get(status, 0) + 1

    # Enquanto o worker processa, verifica responsividade do servidor
    health_times = []
    poll_start = time.perf_counter()
    max_wait = 180  # 3 min: 15 leituras × stub ~3-8s/seção × 15 seções / 4 pool ≈ 15-30 rodadas
    done_count = 0
    polls = 0

    while time.perf_counter() - poll_start < max_wait:
        t0 = time.perf_counter()
        h = await client.get("/api/health")
        health_times.append(time.perf_counter() - t0)
        polls += 1

        # Conta leituras prontas
        db = SessionLocal()
        try:
            done_count = db.scalars(
                select(Reading).where(
                    Reading.content_id == content_id,
                    Reading.status.in_(["ready", "fallback"]),
                )
            ).all()
            done_count = len(done_count)
        finally:
            db.close()

        if done_count >= len(users_tokens):
            break

        await asyncio.sleep(2)

    result.notes.append(
        f"Leituras concluídas: {done_count}/{len(users_tokens)} em "
        f"{time.perf_counter() - poll_start:.0f}s"
    )
    if health_times:
        p95_health = round(_percentile(health_times, 95), 3)
        result.notes.append(
            f"Health check p95 durante geração: {p95_health}s ({polls} verificações)"
        )
        if p95_health > 2.0:
            result.defects.append(
                f"Servidor lento durante geração: health p95={p95_health}s > 2s"
            )

    if done_count < len(users_tokens):
        stuck = len(users_tokens) - done_count
        result.defects.append(
            f"{stuck} leituras não concluíram em {max_wait}s – possível deadlock/timeout"
        )

    # Verifica fallbacks
    db = SessionLocal()
    try:
        fallbacks = db.scalars(
            select(Reading).where(
                Reading.content_id == content_id,
                Reading.status == "fallback",
            )
        ).all()
        if fallbacks:
            result.notes.append(
                f"{len(fallbacks)} leituras em fallback (pool de seções com stub)"
            )
    finally:
        db.close()

    return result


async def scenario_5_duplicate_webhook(client: httpx.AsyncClient) -> ScenarioResult:
    """C5: Mesmo evento entregue 3x — não pode duplicar acesso nem receita."""
    result = ScenarioResult(
        name="C5 – Webhook duplicado (mesmo evento 3×)",
        description="Mesmo event_id → 3 POSTs; 1 deve criar, 2 devem ser duplicate=true",
    )

    payment_id = f"pay-dup-{uuid.uuid4().hex[:12]}"
    email = f"dup-wh-{uuid.uuid4().hex[:8]}@sim.example.com"
    # GG Checkout: mesmo payment.id enviado 3× — deve criar 1 WebhookEvent, 2 duplicates
    gg_payload = {
        "webhook": {"id": f"wh-{payment_id}"},
        "payment": {"id": payment_id, "status": "paid"},
        "customer": {"email": email, "name": "Cliente Dup"},
        "product": {"id": "gg-sim-product"},
        "products": [{"id": "gg-sim-product"}],
    }

    # Envia os 3 de forma SIMULTÂNEA para testar a race condition
    async def one():
        return await client.post("/api/webhooks/ggcheckout", json=gg_payload)

    resps = await asyncio.gather(one(), one(), one())
    statuses = [r.status_code for r in resps]
    bodies = [r.json() if r.headers.get("content-type", "").startswith("application/json")
              else r.text for r in resps]

    ok_count = sum(1 for s in statuses if s == 200)
    dup_count = sum(1 for b in bodies
                    if isinstance(b, dict) and b.get("duplicate") is True)
    error_count = sum(1 for s in statuses if s >= 500)

    result.notes.append(f"Statuses: {statuses}")
    result.notes.append(f"duplicate=true: {dup_count}/3")

    if error_count > 0:
        result.defects.append(
            f"GRAVE: {error_count} resposta(s) 5xx no webhook duplicado – race não foi tratada. "
            f"Statuses: {statuses}. Bodies: {[str(b)[:80] for b in bodies]}"
        )
        result.failures = error_count
        result.successes = ok_count
    else:
        result.successes = ok_count

    # Verifica no DB: exatamente 1 WebhookEvent, 1 User, 1 entitlement
    # GG usa event_id = f"gg:{payment_id}" (ver ggcheckout.py:event_id = f"gg:{payment_id}")
    gg_event_id = f"gg:{payment_id}"
    db = SessionLocal()
    try:
        events = db.scalars(
            select(WebhookEvent).where(
                WebhookEvent.provider == "ggcheckout", WebhookEvent.event_id == gg_event_id
            )
        ).all()
        users = db.scalars(select(User).where(User.email == email)).all()
        ents = []
        if users:
            ents = db.scalars(
                select(Entitlement).where(Entitlement.user_id == users[0].id)
            ).all()

        if len(events) != 1:
            result.defects.append(
                f"GRAVE: {len(events)} WebhookEvents para mesmo event_id (esperado 1)"
            )
        if len(users) > 1:
            result.defects.append(
                f"GRAVE: {len(users)} Users para {email} (race duplicou conta)"
            )
        product_ids = [e.product_id for e in ents]
        if len(product_ids) != len(set(product_ids)):
            result.defects.append(
                f"GRAVE: entitlements duplicados para {email}: {product_ids}"
            )

        result.notes.append(
            f"DB: {len(events)} eventos, {len(users)} users, {len(ents)} entitlements"
        )
    finally:
        db.close()

    return result


async def scenario_6_homonymous_city(client: httpx.AsyncClient) -> ScenarioResult:
    """C6: Cidade homônima com estado diferente — coordenadas devem ser corretas."""
    result = ScenarioResult(
        name="C6 – Cidade homônima com estado diferente",
        description="Santa Cruz/SC vs Santa Cruz/RJ vs Córdoba/AR — coordenadas corretas?",
    )

    # Casos: mesmo nome, estado diferente → lat/lon devem diferir
    cases = [
        # (city, state, country, desc, lat_approx, lon_approx)
        ("Santa Cruz", "SC", "BR", "Santa Cruz/SC Brasil",  -27.8, -50.0),
        ("Santa Cruz", "RJ", "BR", "Santa Cruz/RJ Brasil",  -22.9, -43.7),
        ("Córdoba",    None, "AR", "Córdoba Argentina",     -31.4, -64.2),
        ("Córdoba",    "CBA","AR", "Córdoba/CBA Argentina", -31.4, -64.2),
    ]

    for city, state, country, desc, lat_exp, lon_exp in cases:
        payload = {
            "name": "Astral Tester",
            "birth_date": "1990-06-21",
            "birth_city": city,
            "birth_country": country,
            "locale": "es-AR" if country == "AR" else "pt-BR",
        }
        if state:
            payload["birth_state"] = state

        t0 = time.perf_counter()
        resp = await client.post("/api/horoscopo/gratis", json=payload)
        elapsed = time.perf_counter() - t0
        result.latencies.append(elapsed)

        if resp.status_code == 200:
            result.successes += 1
            result.notes.append(f"{desc}: OK ({elapsed:.2f}s)")
        elif resp.status_code == 422:
            body = resp.json()
            result.failures += 1
            result.defects.append(f"Cidade não resolvida: {desc} → 422: {body.get('detail', '')}")
        else:
            result.failures += 1
            result.defects.append(f"{desc}: status {resp.status_code}")

    # Caso específico: "Santa Fé" na Argentina vs "Santa Fe" (sem acento)
    for city in ["Santa Fe", "Santa Fé"]:
        payload = {
            "name": "Test", "birth_date": "1990-01-01",
            "birth_city": city, "birth_country": "AR", "locale": "es-AR",
        }
        resp = await client.post("/api/horoscopo/gratis", json=payload)
        result.latencies.append(0)
        if resp.status_code == 200:
            result.successes += 1
            result.notes.append(f"'{city}'/AR: OK")
        else:
            result.failures += 1
            result.defects.append(f"'{city}'/AR: {resp.status_code}")

    return result


async def scenario_7_restart_recovery(client: httpx.AsyncClient) -> ScenarioResult:
    """C7: Reinício do worker com leitura em andamento — deve recuperar ou ficar presa?"""
    result = ScenarioResult(
        name="C7 – Reinício do worker durante geração",
        description="Job travado com locked_at antigo → worker novo deve pegar após timeout",
    )

    # 7a. Cria usuário com entitlement e perfil
    email = f"restart-{uuid.uuid4().hex[:8]}@sim.example.com"
    u = _create_user_in_db(email, "senha12345", "Reiniciadora", "pt-BR")
    _grant_entitlement(u.id, "site:mapa_astral")
    _set_profile(u.id, "São Paulo", birth_date="1985-07-04", birth_time="10:00:00")

    # 7b. Insere um Reading + GenerationJob simulando worker morreu durante execução
    db = SessionLocal()
    try:
        reading = Reading(
            user_id=u.id,
            content_id="site:content:mapa_astral_completo",
            product_id="site:mapa_astral",
            status="in_progress",
            title="Mapa Astral Completo",
            input_snapshot={"birth_city": "São Paulo"},
            sections_total=15,
        )
        db.add(reading)
        db.flush()
        rid = reading.id

        # Job simulando worker morreu há 2 minutos (> JOB_VISIBILITY_TIMEOUT_MINUTES=1)
        job = GenerationJob(
            reading_id=rid,
            content_id="site:content:mapa_astral_completo",
            user_id=u.id,
            locale="pt-BR",
            customer_name=u.name,
            status="running",
            attempts=1,
            locked_at=datetime.now(timezone.utc) - timedelta(minutes=2),
            locked_by="dead-worker-sim",
        )
        db.add(job)
        db.commit()
        jid = job.id
    finally:
        db.close()

    result.notes.append(f"Job travado criado: {jid[:8]}… (locked_at 2min atrás)")

    # 7c. Verifica estado inicial: job deve estar "running" com locked_by=dead-worker
    db = SessionLocal()
    try:
        j = db.get(GenerationJob, jid)
        result.notes.append(f"Estado inicial: status={j.status} locked_by={j.locked_by}")
    finally:
        db.close()

    # 7d. Inicia worker (que deve detectar job travado em JOB_VISIBILITY_TIMEOUT=1min)
    # Como o locked_at já está 2min atrás, o worker deve pegar imediatamente
    wt = _start_worker()
    await asyncio.sleep(5)  # Dá tempo de pegar o job

    db = SessionLocal()
    try:
        j = db.get(GenerationJob, jid)
        r = db.get(Reading, rid)
        result.notes.append(
            f"Após 5s: job.status={j.status} job.locked_by={j.locked_by} "
            f"job.attempts={j.attempts} reading.status={r.status}"
        )
        if j.locked_by == "dead-worker-sim" and j.status == "running":
            result.notes.append("Job ainda não reclamado em 5s — aguardando worker inicializar...")
    finally:
        db.close()

    # 7e. Aguarda conclusão (stub leva 3-8s por seção × 15 seções / 4 pool ≈ 45s max)
    deadline = time.perf_counter() + 120
    while time.perf_counter() < deadline:
        await asyncio.sleep(3)
        db = SessionLocal()
        try:
            r = db.get(Reading, rid)
            j = db.get(GenerationJob, jid)
            if r.status in ("ready", "fallback") or j.status in ("done", "failed"):
                result.notes.append(
                    f"Concluído: reading.status={r.status} job.status={j.status} "
                    f"attempts={j.attempts}"
                )
                result.successes = 1
                break
        finally:
            db.close()
    else:
        result.failures = 1
        result.defects.append(
            "GRAVE: Leitura recuperada não terminou em 120s – stuck in_progress"
        )
        db = SessionLocal()
        try:
            r = db.get(Reading, rid)
            j = db.get(GenerationJob, jid)
            result.notes.append(
                f"Final: reading.status={r.status} job.status={j.status}"
            )
        finally:
            db.close()

    _stop_worker()
    return result


async def run_all_scenarios() -> list[ScenarioResult]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                  follow_redirects=True, timeout=30) as client:
        results = []

        # C1: 30 horóscopo grátis simultâneos (pt-BR)
        print("→ C1: horóscopo grátis (pt-BR, 30 simultâneos)…", flush=True)
        _start_worker()
        r = await scenario_1_free_horoscope(client, n=30, locale="pt-BR")
        _stop_worker()
        results.append(r)
        print(f"  {r.successes}/{r.total} OK  p50={r.p50}s p95={r.p95}s", flush=True)

        # C2: 20 trials simultâneos (pt-BR)
        print("→ C2: trials simultâneos (pt-BR, 20)…", flush=True)
        r = await scenario_2_trials(client, n=20, locale="pt-BR")
        results.append(r)
        print(f"  {r.successes}/{r.total} OK  falhas={r.failures}", flush=True)

        # C3: 10 webhooks simultâneos (pt-BR)
        print("→ C3: webhooks compra simultâneos (pt-BR, 10)…", flush=True)
        r = await scenario_3_webhooks(client, n=10, locale="pt-BR")
        results.append(r)
        print(f"  {r.successes}/{r.total} OK  falhas={r.failures}", flush=True)

        # C4: 15 gerações concorrentes de Mapa Astral
        print("→ C4: 15 gerações simultâneas de Mapa Astral…", flush=True)
        _start_worker()
        r = await scenario_4_concurrent_generation(client, n=15)
        _stop_worker()
        results.append(r)
        print(f"  {r.successes}/{r.total} /generate OK  defects={len(r.defects)}", flush=True)

        # C5: Webhook duplicado
        print("→ C5: webhook duplicado (3× mesmo evento)…", flush=True)
        r = await scenario_5_duplicate_webhook(client)
        results.append(r)
        print(f"  defects={len(r.defects)}", flush=True)

        # C6: Cidade homônima
        print("→ C6: cidade homônima com estado diferente…", flush=True)
        r = await scenario_6_homonymous_city(client)
        results.append(r)
        print(f"  {r.successes}/{r.total} resolvidas  defects={len(r.defects)}", flush=True)

        # C7: Restart do worker
        print("→ C7: restart do worker com job travado…", flush=True)
        r = await scenario_7_restart_recovery(client)
        results.append(r)
        print(f"  successes={r.successes}  defects={len(r.defects)}", flush=True)

        # C8: Repetição de C1+C2+C3 em es-AR
        print("→ C8a: horóscopo grátis (es-AR, 30)…", flush=True)
        _start_worker()
        r = await scenario_1_free_horoscope(client, n=30, locale="es-AR")
        _stop_worker()
        results.append(r)
        print(f"  {r.successes}/{r.total} OK", flush=True)

        print("→ C8b: trials simultâneos (es-AR, 20)…", flush=True)
        r = await scenario_2_trials(client, n=20, locale="es-AR")
        results.append(r)
        print(f"  {r.successes}/{r.total} OK", flush=True)

        print("→ C8c: webhooks compra simultâneos (es-AR, 10)…", flush=True)
        r = await scenario_3_webhooks(client, n=10, locale="es-AR")
        results.append(r)
        print(f"  {r.successes}/{r.total} OK", flush=True)

    return results


# ── 10. Relatório ─────────────────────────────────────────────────────────────


def _severity(defect: str) -> str:
    if "GRAVE" in defect:
        return "🔴"
    if "Atenção" in defect or "lento" in defect.lower():
        return "🟡"
    return "🔴"


def generate_report(results: list[ScenarioResult]) -> str:
    all_defects: list[tuple[str, str]] = []  # (scenario_name, defect)
    for r in results:
        for d in r.defects:
            all_defects.append((r.name, d))

    lines = [
        "# Relatório de Simulação de Clientes — AstroDicas",
        "",
        f"**Data:** {datetime.now().strftime('%Y-%m-%d %H:%M')}  ",
        f"**Banco:** data/sim.db (isolado, não toca dev.db)  ",
        f"**Chamadas MiniMax reais:** {_real_call_count}/{_MAX_REAL_CALLS} (restante: stub com 3-8s/seção simulados)  ",
        f"**Chamadas MiniMax stub:** estimado ~{max(0, sum(1 for r in results for _ in r.latencies) * 15)} (15 seções × n leituras)  ",
        "",
        "---",
        "",
        "## Tabela de Cenários",
        "",
        "| # | Cenário | OK | Falhas | p50 | p95 | Defeitos |",
        "|---|---------|----|----|-----|-----|----------|",
    ]

    for i, r in enumerate(results, 1):
        defect_summary = len(r.defects)
        flag = "✅" if (defect_summary == 0 and r.failures == 0) else "❌"
        p50 = f"{r.p50}s" if r.latencies else "—"
        p95 = f"{r.p95}s" if r.latencies else "—"
        lines.append(
            f"| {flag} | {r.name} | {r.successes} | {r.failures} "
            f"| {p50} | {p95} | {defect_summary} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Detalhes por Cenário",
        "",
    ]

    for r in results:
        lines += [f"### {r.name}", "", f"_{r.description}_", ""]
        if r.notes:
            lines.append("**Observações:**")
            for note in r.notes:
                lines.append(f"- {note}")
            lines.append("")
        if r.errors:
            lines.append(f"**HTTP errors:** {r.errors}")
            lines.append("")
        if r.defects:
            lines.append("**Defeitos encontrados:**")
            for d in r.defects:
                lines.append(f"- {_severity(d)} {d}")
            lines.append("")
        if not r.defects and not r.errors:
            lines.append("✅ Nenhum defeito encontrado.")
            lines.append("")

    lines += [
        "---",
        "",
        "## Lista de Defeitos por Gravidade",
        "",
        "> Ordenados: primeiro o que faz cliente pagar e não receber.",
        "",
    ]

    if not all_defects:
        lines.append("✅ **Nenhum defeito encontrado em todos os cenários.**")
    else:
        # Grave first, then Atenção
        grave = [(s, d) for s, d in all_defects if "GRAVE" in d]
        atenc = [(s, d) for s, d in all_defects if "Atenção" in d or "lento" in d.lower()]
        outros = [(s, d) for s, d in all_defects if (s, d) not in grave and (s, d) not in atenc]

        if grave:
            lines += ["### 🔴 Gravidade Alta (cliente paga e não recebe / dados corrompidos)", ""]
            for i, (scenario, d) in enumerate(grave, 1):
                lines.append(f"**{i}.** [{scenario}]  ")
                lines.append(f"   {d}")
                lines.append("")

        if atenc:
            lines += ["### 🟡 Gravidade Média (degradação de serviço)", ""]
            for i, (scenario, d) in enumerate(atenc, 1):
                lines.append(f"**{i}.** [{scenario}]  ")
                lines.append(f"   {d}")
                lines.append("")

        if outros:
            lines += ["### ⚪ Gravidade Baixa / Observações", ""]
            for i, (scenario, d) in enumerate(outros, 1):
                lines.append(f"**{i}.** [{scenario}]  ")
                lines.append(f"   {d}")
                lines.append("")

    lines += [
        "---",
        "",
        "## Nota sobre Arquitetura (C4)",
        "",
        "**Gargalo identificado (não novo — documentado no código):** cada leitura usa um pool interno de",
        f"4 threads para seções, sem teto global. {15} leituras simultâneas = até 60 threads de seção",
        "concorrentes. O worker mitiga (MAX_CONCURRENCY=8 jobs em paralelo) mas o pool de seção",
        "multiplica: 8 jobs × 4 seções = 32 chamadas MiniMax simultâneas em pico.",
        "Em produção com Postgres e worker separado isso é gerenciável; em SQLite dev pode causar",
        "`database is locked` se o worker não usar WAL mode.",
        "",
    ]

    return "\n".join(lines)


# ── Main ───────────────────────────────────────────────────────────────────────


async def main():
    print("\n=== Simulação de Clientes AstroDicas ===\n", flush=True)
    t0 = time.perf_counter()

    try:
        results = await run_all_scenarios()
    except Exception as exc:
        print(f"\n[ERRO FATAL] {exc}", flush=True)
        raise

    elapsed = time.perf_counter() - t0
    print(f"\n✓ Simulação concluída em {elapsed:.0f}s", flush=True)

    report = generate_report(results)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"✓ Relatório: {REPORT_PATH}", flush=True)

    total_defects = sum(len(r.defects) for r in results)
    print(f"\nDefeitos encontrados: {total_defects}", flush=True)
    if total_defects > 0:
        print("\nDefeitos:", flush=True)
        for r in results:
            for d in r.defects:
                print(f"  [{r.name[:30]}] {d[:100]}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
