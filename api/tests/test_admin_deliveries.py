"""Acompanhamento de entregas do dia no admin — fallback manual.

A tela não é o pipeline: o worker continua com seu backoff e suas 3 tentativas.
Estes testes fixam justamente a fronteira — o botão manual só age onde o
automático já desistiu, e nunca duplica o que ainda está na fila.
"""

import os
from datetime import datetime, time, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.admin import router as admin_router
from app.db import SessionLocal
from app.models import GenerationJob, Reading, User
from app.security import hash_password

os.environ["ADMIN_PASSWORD"] = "letmein-test"

SP = ZoneInfo("America/Sao_Paulo")

DIARIO = "site:diario_astral"
MAPA = "site:mapa_astral"
CONTENT_DIARIO = "site:content:horoscopo_diario"
CONTENT_MAPA = "site:content:mapa_astral_completo"


@pytest.fixture()
def admin_client(clean_database):
    app = FastAPI()
    app.include_router(admin_router)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def authed_admin(admin_client):
    r = admin_client.post("/api/admin/login", json={"password": "letmein-test"})
    assert r.status_code == 200, r.text
    return admin_client


def _today_sp_utc(hour: int = 10) -> datetime:
    """Instante UTC que cai no dia de hoje em São Paulo."""
    today = datetime.now(SP).date()
    return datetime.combine(today, time(hour, 0), tzinfo=SP).astimezone(timezone.utc)


def _seed(
    email: str,
    *,
    product_id: str = DIARIO,
    content_id: str = CONTENT_DIARIO,
    reading_status: str = "failed",
    created_at: datetime | None = None,
    job_status: str | None = None,
    job_attempts: int = 0,
    job_not_before: datetime | None = None,
    job_last_error: str | None = None,
) -> tuple[str, str]:
    """Cria usuário + leitura (+ job opcional). Devolve (user_id, reading_id)."""
    db = SessionLocal()
    try:
        user = User(
            id=str(uuid4()),
            email=email,
            password_hash=hash_password("senha"),
            name=email.split("@")[0],
            locale="pt-BR",
        )
        db.add(user)
        db.flush()
        reading = Reading(
            id=str(uuid4()),
            user_id=user.id,
            content_id=content_id,
            product_id=product_id,
            status=reading_status,
            title="Entrega de teste",
            created_at=created_at or _today_sp_utc(),
            sections_total=3,
            sections_done=0,
            error_message="erro de teste " + ("x" * 400),
        )
        db.add(reading)
        db.flush()
        if job_status:
            db.add(GenerationJob(
                id=str(uuid4()),
                reading_id=reading.id,
                content_id=content_id,
                user_id=user.id,
                locale="pt-BR",
                status=job_status,
                attempts=job_attempts,
                not_before=job_not_before or datetime.now(timezone.utc),
                last_error=job_last_error,
            ))
        db.commit()
        return user.id, reading.id
    finally:
        db.close()


def _flat(payload: dict) -> list[dict]:
    return [item for group in payload["products"] for item in group["items"]]


def _row(payload: dict, email: str) -> dict:
    return next(item for item in _flat(payload) if item["email"] == email)


# ---------------------------------------------------------------------------
# Segurança: nenhuma rota sem sessão admin
# ---------------------------------------------------------------------------

def test_deliveries_requires_auth(admin_client):
    assert admin_client.get("/api/admin/deliveries").status_code == 401


def test_deliveries_summary_requires_auth(admin_client):
    assert admin_client.get("/api/admin/deliveries/summary").status_code == 401


def test_delivery_retry_requires_auth(admin_client):
    assert admin_client.post("/api/admin/deliveries/qualquer-id/retry").status_code == 401


def test_retry_batch_requires_auth(admin_client):
    r = admin_client.post("/api/admin/deliveries/retry-batch", json={"statuses": ["failed"]})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Filtro por dia e por produto
# ---------------------------------------------------------------------------

def test_deliveries_defaults_to_today_sao_paulo(authed_admin):
    _seed("hoje@example.com")
    _seed("ontem@example.com", created_at=_today_sp_utc() - timedelta(days=1))

    payload = authed_admin.get("/api/admin/deliveries").json()
    emails = {item["email"] for item in _flat(payload)}
    assert emails == {"hoje@example.com"}
    assert payload["timezone"] == "America/Sao_Paulo"
    assert payload["date"] == datetime.now(SP).date().isoformat()


def test_deliveries_accepts_explicit_date(authed_admin):
    yesterday = datetime.now(SP).date() - timedelta(days=1)
    _seed("ontem@example.com", created_at=_today_sp_utc() - timedelta(days=1))
    _seed("hoje@example.com")

    payload = authed_admin.get("/api/admin/deliveries", params={"date": yesterday.isoformat()}).json()
    assert {item["email"] for item in _flat(payload)} == {"ontem@example.com"}


def test_deliveries_late_night_belongs_to_sao_paulo_day(authed_admin):
    """23h em SP ainda é hoje, mesmo já sendo o dia seguinte em UTC."""
    today = datetime.now(SP).date()
    late = datetime.combine(today, time(23, 30), tzinfo=SP).astimezone(timezone.utc)
    _seed("tarde@example.com", created_at=late)

    payload = authed_admin.get("/api/admin/deliveries", params={"date": today.isoformat()}).json()
    assert {item["email"] for item in _flat(payload)} == {"tarde@example.com"}


def test_deliveries_filters_by_product(authed_admin):
    _seed("diario@example.com", product_id=DIARIO, content_id=CONTENT_DIARIO)
    _seed("mapa@example.com", product_id=MAPA, content_id=CONTENT_MAPA)

    payload = authed_admin.get("/api/admin/deliveries", params={"product_id": MAPA}).json()
    assert {item["email"] for item in _flat(payload)} == {"mapa@example.com"}


def test_deliveries_rejects_bad_date(authed_admin):
    assert authed_admin.get("/api/admin/deliveries", params={"date": "14-08-2026"}).status_code == 400


# ---------------------------------------------------------------------------
# Agrupamento, contagem e ordenação por urgência
# ---------------------------------------------------------------------------

def test_deliveries_groups_by_product_with_counts(authed_admin):
    _seed("ok@example.com", product_id=DIARIO, reading_status="ready", job_status="done")
    _seed("erro@example.com", product_id=DIARIO, job_status="failed", job_attempts=3)
    _seed("fila@example.com", product_id=MAPA, content_id=CONTENT_MAPA,
          reading_status="pending", job_status="queued", job_attempts=1)

    payload = authed_admin.get("/api/admin/deliveries").json()
    groups = {g["product_id"]: g for g in payload["products"]}

    assert payload["total"] == 3
    assert groups[DIARIO]["total"] == 2
    assert groups[DIARIO]["done"] == 1
    assert groups[DIARIO]["failed"] == 1
    assert groups[DIARIO]["needs_human"] == 1
    assert groups[MAPA]["pending"] == 1
    assert groups[MAPA]["auto_will_retry"] == 1
    assert groups[MAPA]["needs_human"] == 0


def test_deliveries_lists_client_identity_and_truncated_error(authed_admin):
    _seed("erro@example.com", job_status="failed", job_attempts=3, job_last_error="boom " * 200)
    row = _row(authed_admin.get("/api/admin/deliveries").json(), "erro@example.com")

    assert row["name"] == "erro"
    assert row["content_id"] == CONTENT_DIARIO
    assert row["attempts"] == 3
    assert len(row["error_message"]) <= 301
    assert len(row["last_error"]) <= 301
    assert row["created_at"]


def test_deliveries_orders_problems_first(authed_admin):
    _seed("ok@example.com", reading_status="ready", job_status="done")
    _seed("fila@example.com", reading_status="pending", job_status="queued")
    _seed("erro@example.com", job_status="failed", job_attempts=3)

    buckets = [item["bucket"] for item in _flat(authed_admin.get("/api/admin/deliveries").json())]
    assert buckets.index("failed") < buckets.index("pending") < buckets.index("done")


def test_deliveries_marks_what_auto_will_still_try(authed_admin):
    """Job em backoff (not_before no futuro) NÃO é caso de mão humana."""
    future = datetime.now(timezone.utc) + timedelta(minutes=15)
    _seed("backoff@example.com", reading_status="pending", job_status="queued",
          job_attempts=2, job_not_before=future)

    row = _row(authed_admin.get("/api/admin/deliveries").json(), "backoff@example.com")
    assert row["auto_will_retry"] is True
    assert row["auto_gave_up"] is False
    assert row["needs_human"] is False
    assert row["next_attempt_at"] is not None
    assert "automático ainda vai tentar" in row["auto_note"]


def test_deliveries_marks_what_auto_gave_up_on(authed_admin):
    _seed("desistiu@example.com", job_status="failed", job_attempts=3)
    row = _row(authed_admin.get("/api/admin/deliveries").json(), "desistiu@example.com")
    assert row["auto_gave_up"] is True
    assert row["needs_human"] is True
    assert "desistiu" in row["auto_note"]


def test_deliveries_flags_orphan_reading_without_job(authed_admin):
    _seed("orfa@example.com", reading_status="pending", job_status=None)
    row = _row(authed_admin.get("/api/admin/deliveries").json(), "orfa@example.com")
    assert row["bucket"] == "orphan"
    assert row["needs_human"] is True
    assert row["auto_will_retry"] is False


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def test_summary_counts_by_product_and_status(authed_admin):
    _seed("ok@example.com", product_id=DIARIO, reading_status="ready", job_status="done")
    _seed("erro@example.com", product_id=DIARIO, reading_status="failed", job_status="failed", job_attempts=3)
    _seed("fila@example.com", product_id=MAPA, content_id=CONTENT_MAPA,
          reading_status="pending", job_status="running")

    body = authed_admin.get("/api/admin/deliveries/summary").json()
    assert body["totals"] == {
        "total": 3, "done": 1, "pending": 1, "failed": 1,
        "orphan": 0, "needs_human": 1, "auto_will_retry": 1,
    }
    assert body["by_status"] == {"ready": 1, "failed": 1, "pending": 1}
    by_product = {p["product_id"]: p for p in body["by_product"]}
    assert by_product[DIARIO]["total"] == 2
    assert by_product[MAPA]["auto_will_retry"] == 1
    assert body["retry_batch_max"] == 200


def test_summary_respects_date_and_product_filters(authed_admin):
    _seed("ontem@example.com", created_at=_today_sp_utc() - timedelta(days=1))
    _seed("hoje@example.com", product_id=MAPA, content_id=CONTENT_MAPA)

    body = authed_admin.get("/api/admin/deliveries/summary", params={"product_id": MAPA}).json()
    assert body["totals"]["total"] == 1


# ---------------------------------------------------------------------------
# Retry individual
# ---------------------------------------------------------------------------

def test_retry_single_enqueues_and_marks_manual(authed_admin, skip_auto_run):
    _, reading_id = _seed("desistiu@example.com", job_status="failed", job_attempts=3)

    r = authed_admin.post(f"/api/admin/deliveries/{reading_id}/retry")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["new_reading_id"] != reading_id
    assert body["triggered_by"] == "manual"

    db = SessionLocal()
    try:
        assert db.get(Reading, reading_id).status == "superseded"
        job = db.get(GenerationJob, body["job_id"])
        assert job.triggered_by == "manual"
        assert job.triggered_by_admin == "admin"
        assert job.triggered_at is not None
    finally:
        db.close()


def test_retry_single_not_found(authed_admin):
    assert authed_admin.post("/api/admin/deliveries/nao-existe/retry").status_code == 404


def test_retry_single_refuses_to_compete_with_auto(authed_admin, skip_auto_run):
    """Job aberto = automático ainda vai tentar. Manual não duplica."""
    _, reading_id = _seed("fila@example.com", reading_status="pending", job_status="queued")

    r = authed_admin.post(f"/api/admin/deliveries/{reading_id}/retry")
    assert r.status_code == 409
    assert "automático" in r.json()["detail"]


def test_auto_pipeline_job_stays_marked_auto(authed_admin, skip_auto_run):
    """Job criado fora do painel continua triggered_by='auto' (default)."""
    _seed("auto@example.com", reading_status="pending", job_status="queued")
    db = SessionLocal()
    try:
        job = db.query(GenerationJob).one()
        assert job.triggered_by == "auto"
        assert job.triggered_by_admin is None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Retry em lote — idempotência e teto
# ---------------------------------------------------------------------------

def test_retry_batch_enqueues_failed_only_when_asked(authed_admin, skip_auto_run):
    _seed("erro@example.com", job_status="failed", job_attempts=3)
    _seed("ok@example.com", reading_status="ready", job_status="done")

    body = authed_admin.post("/api/admin/deliveries/retry-batch", json={"statuses": ["failed"]}).json()
    assert body["enqueued_count"] == 1
    assert body["enqueued"][0]["email"] == "erro@example.com"
    assert body["estimated_requests"] >= 1


def test_retry_batch_skips_items_auto_will_retry(authed_admin, skip_auto_run):
    """Idempotência: job aberto nunca vira job duplicado."""
    _seed("erro@example.com", job_status="failed", job_attempts=3)
    _seed("fila@example.com", reading_status="pending", job_status="queued")

    body = authed_admin.post(
        "/api/admin/deliveries/retry-batch", json={"statuses": ["failed", "pending"]}
    ).json()

    assert body["enqueued_count"] == 1
    assert body["skipped_count"] == 1
    skipped = body["skipped"][0]
    assert skipped["email"] == "fila@example.com"
    assert skipped["reason"] == "auto_will_retry"

    db = SessionLocal()
    try:
        # nenhum job novo para o usuário que o automático ainda vai atender
        jobs = db.query(GenerationJob).filter(GenerationJob.status == "queued").all()
        assert len({j.user_id for j in jobs}) == 2  # o da fila + o recém-criado
        assert sum(1 for j in jobs if j.triggered_by == "manual") == 1
    finally:
        db.close()


def test_retry_batch_is_idempotent_on_second_call(authed_admin, skip_auto_run):
    _seed("erro@example.com", job_status="failed", job_attempts=3)

    first = authed_admin.post("/api/admin/deliveries/retry-batch", json={"statuses": ["failed"]}).json()
    assert first["enqueued_count"] == 1

    second = authed_admin.post("/api/admin/deliveries/retry-batch", json={"statuses": ["failed"]}).json()
    assert second["enqueued_count"] == 0

    db = SessionLocal()
    try:
        manual_jobs = db.query(GenerationJob).filter(GenerationJob.triggered_by == "manual").all()
        assert len(manual_jobs) == 1
    finally:
        db.close()


def test_retry_batch_respects_limit_cap(authed_admin, skip_auto_run):
    for i in range(5):
        _seed(f"erro{i}@example.com", job_status="failed", job_attempts=3)

    body = authed_admin.post(
        "/api/admin/deliveries/retry-batch", json={"statuses": ["failed"], "limit": 2}
    ).json()

    assert body["limit"] == 2
    assert body["enqueued_count"] == 2
    assert body["remaining"] == 3
    assert all(s["reason"] == "batch_limit" for s in body["skipped"])


def test_retry_batch_limit_never_exceeds_configured_max(authed_admin, skip_auto_run, monkeypatch):
    monkeypatch.setenv("ADMIN_RETRY_BATCH_MAX", "1")
    for i in range(3):
        _seed(f"erro{i}@example.com", job_status="failed", job_attempts=3)

    body = authed_admin.post(
        "/api/admin/deliveries/retry-batch", json={"statuses": ["failed"], "limit": 999}
    ).json()

    assert body["batch_max"] == 1
    assert body["limit"] == 1
    assert body["enqueued_count"] == 1
    assert body["remaining"] == 2


def test_retry_batch_default_cap_is_200(authed_admin, skip_auto_run):
    body = authed_admin.post("/api/admin/deliveries/retry-batch", json={"statuses": ["failed"]}).json()
    assert body["batch_max"] == 200
    assert body["limit"] == 200


def test_retry_batch_filters_by_product_and_date(authed_admin, skip_auto_run):
    _seed("diario@example.com", product_id=DIARIO, job_status="failed", job_attempts=3)
    _seed("mapa@example.com", product_id=MAPA, content_id=CONTENT_MAPA,
          job_status="failed", job_attempts=3)

    body = authed_admin.post(
        "/api/admin/deliveries/retry-batch",
        json={"statuses": ["failed"], "product_id": MAPA},
    ).json()

    assert body["enqueued_count"] == 1
    assert body["enqueued"][0]["email"] == "mapa@example.com"


def test_retry_batch_ignores_yesterday(authed_admin, skip_auto_run):
    _seed("ontem@example.com", created_at=_today_sp_utc() - timedelta(days=1),
          job_status="failed", job_attempts=3)

    body = authed_admin.post("/api/admin/deliveries/retry-batch", json={"statuses": ["failed"]}).json()
    assert body["enqueued_count"] == 0


def test_retry_batch_rejects_bad_statuses(authed_admin):
    r = authed_admin.post("/api/admin/deliveries/retry-batch", json={"statuses": "failed"})
    assert r.status_code == 400


def test_leitura_substituida_nao_conta_como_entrega_pendente(authed_admin):
    """'superseded' é versão antiga trocada de propósito por uma regeneração —
    a cliente já tem a nova. Medido em produção (18/08/2026): uma bateria de
    regenerações deixou 12 pendentes falsas contra 4 entregas reais. Painel de
    operação com alarme falso deixa de ser lido, e some o alarme verdadeiro
    junto."""
    _seed("substituida@example.com", product_id=MAPA, content_id=CONTENT_MAPA,
          reading_status="superseded", job_status="done")
    _seed("entregue@example.com", product_id=MAPA, content_id=CONTENT_MAPA,
          reading_status="ready", job_status="done")

    r = authed_admin.get("/api/admin/deliveries/summary")
    assert r.status_code == 200, r.text
    totais = r.json()["totals"]
    assert totais["pending"] == 0, "leitura substituída não é entrega pendente"
    assert totais["done"] == 1

    linhas = authed_admin.get("/api/admin/deliveries").json()
    emails = [it["email"] for p in linhas["products"] for it in p["items"]]
    assert "substituida@example.com" not in emails
    assert "entregue@example.com" in emails
