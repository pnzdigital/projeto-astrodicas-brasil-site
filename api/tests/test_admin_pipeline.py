"""Testes para /api/admin/pipeline-status, /api/admin/clients e
/api/admin/users/{id}/detail — rotas novas do painel reformado.

TDD: estes testes devem ser escritos antes da implementação e falhar
até que as rotas sejam adicionadas ao admin.py.
"""

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.admin import router as admin_router
from app.db import SessionLocal
from app.models import Entitlement, GenerationJob, Order, Reading, User
from app.security import hash_password

os.environ["ADMIN_PASSWORD"] = "letmein-test"


@pytest.fixture()
def admin_client(clean_database):
    app = FastAPI()
    app.include_router(admin_router)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def authed(admin_client):
    r = admin_client.post("/api/admin/login", json={"password": "letmein-test"})
    assert r.status_code == 200
    return admin_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(db, email="user@test.com"):
    user = User(
        id=str(uuid4()),
        email=email,
        password_hash=hash_password("senha"),
        name=email.split("@")[0],
        locale="pt-BR",
    )
    db.add(user)
    db.flush()
    return user


def _make_reading(
    db,
    user_id,
    *,
    status="ready",
    body_html="<p>ok</p>",
    content_id="site:content:mapa_astral_completo",
    product_id="site:mapa_astral",
    updated_at=None,
):
    r = Reading(
        id=str(uuid4()),
        user_id=user_id,
        content_id=content_id,
        product_id=product_id,
        status=status,
        body_html=body_html,
        source="llm",
    )
    if updated_at is not None:
        r.updated_at = updated_at
    db.add(r)
    db.flush()
    return r


def _make_job(
    db,
    user_id,
    reading_id,
    *,
    status="failed",
    content_id="site:content:mapa_astral_completo",
):
    job = GenerationJob(
        id=str(uuid4()),
        reading_id=reading_id,
        content_id=content_id,
        user_id=user_id,
        locale="pt-BR",
        customer_name="Test",
        status=status,
    )
    db.add(job)
    db.flush()
    return job


# ---------------------------------------------------------------------------
# /api/admin/pipeline-status
# ---------------------------------------------------------------------------

def test_pipeline_status_requires_auth(admin_client):
    r = admin_client.get("/api/admin/pipeline-status")
    assert r.status_code == 401


def test_pipeline_status_empty(authed):
    r = authed.get("/api/admin/pipeline-status")
    assert r.status_code == 200
    body = r.json()
    assert body["readings_by_status"] == {}
    assert body["jobs_by_status"] == {}
    assert body["critical"]["paid_no_content"] == 0
    assert body["critical"]["stuck_in_progress"] == 0


def test_pipeline_status_counts_readings_by_status(authed):
    db = SessionLocal()
    try:
        u = _make_user(db)
        _make_reading(db, u.id, status="ready")
        _make_reading(db, u.id, status="ready", content_id="site:content:guia_do_mes")
        _make_reading(db, u.id, status="in_progress", content_id="site:content:mapa_da_carreira")
        _make_reading(db, u.id, status="fallback", content_id="site:content:mapa_da_prosperidade")
        db.commit()
    finally:
        db.close()

    r = authed.get("/api/admin/pipeline-status")
    assert r.status_code == 200
    counts = r.json()["readings_by_status"]
    assert counts.get("ready", 0) == 2
    assert counts.get("in_progress", 0) == 1
    assert counts.get("fallback", 0) == 1


def test_pipeline_status_counts_jobs_by_status(authed):
    db = SessionLocal()
    try:
        u = _make_user(db)
        r1 = _make_reading(db, u.id, status="ready")
        r2 = _make_reading(db, u.id, status="pending", content_id="site:content:guia_do_mes")
        _make_job(db, u.id, r1.id, status="done")
        _make_job(db, u.id, r2.id, status="queued")
        db.commit()
    finally:
        db.close()

    r = authed.get("/api/admin/pipeline-status")
    body = r.json()
    assert body["jobs_by_status"].get("done", 0) == 1
    assert body["jobs_by_status"].get("queued", 0) == 1


def test_pipeline_status_paid_no_content_counts_critical_readings(authed):
    """Leitura com job failed e body_html vazio → critical.paid_no_content = 1.

    Leitura superseded com as mesmas condições não deve contar — é histórico,
    não é um cliente sem receber.
    """
    db = SessionLocal()
    try:
        u = _make_user(db)
        # leitura problemática: body_html vazio + job failed
        r1 = _make_reading(db, u.id, status="in_progress", body_html="")
        _make_job(db, u.id, r1.id, status="failed")
        # superseded não deve contar mesmo com body vazio e job failed
        r2 = _make_reading(db, u.id, status="superseded", body_html="", content_id="site:content:guia_do_mes")
        _make_job(db, u.id, r2.id, status="failed")
        db.commit()
    finally:
        db.close()

    resp = authed.get("/api/admin/pipeline-status")
    assert resp.json()["critical"]["paid_no_content"] == 1


def test_pipeline_status_paid_no_content_ignores_readings_with_content(authed):
    """Leitura com job failed mas body_html preenchido não é alerta crítico."""
    db = SessionLocal()
    try:
        u = _make_user(db)
        r1 = _make_reading(db, u.id, status="ready", body_html="<p>conteúdo</p>")
        _make_job(db, u.id, r1.id, status="failed")
        db.commit()
    finally:
        db.close()

    resp = authed.get("/api/admin/pipeline-status")
    assert resp.json()["critical"]["paid_no_content"] == 0


def test_pipeline_status_stuck_in_progress(authed):
    """Leitura in_progress com updated_at >30min atrás → critical.stuck_in_progress = 1."""
    db = SessionLocal()
    try:
        u = _make_user(db)
        old_ts = datetime.now(timezone.utc) - timedelta(minutes=45)
        _make_reading(db, u.id, status="in_progress", body_html="", updated_at=old_ts)
        # Leitura recente NÃO deve contar como travada
        _make_reading(db, u.id, status="in_progress", body_html="", content_id="site:content:guia_do_mes")
        db.commit()
    finally:
        db.close()

    resp = authed.get("/api/admin/pipeline-status")
    assert resp.json()["critical"]["stuck_in_progress"] == 1


# ---------------------------------------------------------------------------
# /api/admin/clients
# ---------------------------------------------------------------------------

def test_clients_requires_auth(admin_client):
    r = admin_client.get("/api/admin/clients")
    assert r.status_code == 401


def test_clients_empty_when_no_users_with_entitlements(authed):
    r = authed.get("/api/admin/clients")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["clients"] == []


def test_clients_returns_one_row_per_user(authed):
    db = SessionLocal()
    try:
        u1 = _make_user(db, "a@test.com")
        u2 = _make_user(db, "b@test.com")
        db.add(Entitlement(id=str(uuid4()), user_id=u1.id, product_id="site:mapa_astral", status="available"))
        db.add(Entitlement(id=str(uuid4()), user_id=u2.id, product_id="site:diario_astral", status="available"))
        _make_reading(db, u1.id, status="ready")
        _make_reading(db, u1.id, status="fallback", body_html="", content_id="site:content:guia_do_mes")
        db.commit()
    finally:
        db.close()

    r = authed.get("/api/admin/clients")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2

    emails = {c["email"] for c in body["clients"]}
    assert "a@test.com" in emails
    assert "b@test.com" in emails

    u1_row = next(c for c in body["clients"] if c["email"] == "a@test.com")
    assert u1_row["entitlement_count"] == 1
    assert u1_row["readings_ready"] == 1
    assert u1_row["readings_fallback"] == 1
    assert u1_row["has_problem"] is True

    u2_row = next(c for c in body["clients"] if c["email"] == "b@test.com")
    assert u2_row["has_problem"] is False


def test_clients_problems_first(authed):
    """Clientes com problema aparecem antes dos sem problema."""
    db = SessionLocal()
    try:
        u_ok = _make_user(db, "ok@test.com")
        u_bad = _make_user(db, "bad@test.com")
        db.add(Entitlement(id=str(uuid4()), user_id=u_ok.id, product_id="site:mapa_astral", status="available"))
        db.add(Entitlement(id=str(uuid4()), user_id=u_bad.id, product_id="site:mapa_astral", status="available"))
        _make_reading(db, u_ok.id, status="ready")
        _make_reading(db, u_bad.id, status="failed", body_html="")
        db.commit()
    finally:
        db.close()

    r = authed.get("/api/admin/clients")
    clients = r.json()["clients"]
    assert clients[0]["email"] == "bad@test.com"


def test_clients_includes_users_with_orders_but_no_entitlement(authed):
    """Usuário com pedido mas sem entitlement ainda aparece na listagem."""
    db = SessionLocal()
    try:
        u = _make_user(db, "buyer@test.com")
        db.add(Order(
            id=str(uuid4()),
            user_id=u.id,
            provider="mercadopago",
            external_id="ext-buyer",
            product_id="site:mapa_astral",
            status="paid",
            amount_minor=4700,
            currency="BRL",
            locale="pt-BR",
            market="BR",
            customer_email=u.email,
        ))
        db.commit()
    finally:
        db.close()

    r = authed.get("/api/admin/clients")
    assert r.json()["total"] == 1


def test_clients_superseded_readings_excluded_from_counts(authed):
    """Leituras superseded não entram na contagem de status."""
    db = SessionLocal()
    try:
        u = _make_user(db)
        db.add(Entitlement(id=str(uuid4()), user_id=u.id, product_id="site:mapa_astral", status="available"))
        _make_reading(db, u.id, status="ready")
        _make_reading(db, u.id, status="superseded", body_html="", content_id="site:content:guia_do_mes")
        db.commit()
    finally:
        db.close()

    r = authed.get("/api/admin/clients")
    row = r.json()["clients"][0]
    # superseded não deve aparecer em nenhum contador
    assert row["readings_total"] == 1
    assert row["readings_ready"] == 1


# ---------------------------------------------------------------------------
# /api/admin/users/{user_id}/detail
# ---------------------------------------------------------------------------

def test_user_detail_404(authed):
    r = authed.get("/api/admin/users/nonexistent-id/detail")
    assert r.status_code == 404


def test_user_detail_returns_entitlements_and_readings(authed):
    db = SessionLocal()
    try:
        u = _make_user(db, "detail@test.com")
        db.add(Entitlement(
            id=str(uuid4()),
            user_id=u.id,
            product_id="site:mapa_astral",
            status="available",
        ))
        _make_reading(db, u.id, status="ready", body_html="<p>conteúdo</p>")
        _make_reading(db, u.id, status="superseded", body_html="", content_id="site:content:guia_do_mes")
        db.commit()
        user_id = u.id
    finally:
        db.close()

    r = authed.get(f"/api/admin/users/{user_id}/detail")
    assert r.status_code == 200
    body = r.json()

    assert body["user"]["id"] == user_id
    assert body["user"]["email"] == "detail@test.com"

    assert len(body["entitlements"]) == 1
    assert body["entitlements"][0]["product_id"] == "site:mapa_astral"

    # Todas as leituras, incluindo superseded (audit trail)
    assert len(body["readings"]) == 2

    ready_r = next(x for x in body["readings"] if x["status"] == "ready")
    assert ready_r["has_content"] is True
    assert ready_r["body_html_len"] > 0

    sup_r = next(x for x in body["readings"] if x["status"] == "superseded")
    assert sup_r["has_content"] is False


def test_user_detail_requires_auth(admin_client):
    r = admin_client.get("/api/admin/users/any-id/detail")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# /api/admin/sales inclui user_id
# ---------------------------------------------------------------------------

def test_sales_row_includes_user_id(authed):
    """Cada linha de venda agora expõe user_id para o front abrir o detalhe."""
    db = SessionLocal()
    try:
        u = _make_user(db, "buyer2@test.com")
        db.add(Order(
            id=str(uuid4()),
            user_id=u.id,
            provider="mercadopago",
            external_id="ext-uid-test",
            product_id="site:mapa_astral",
            status="paid",
            amount_minor=4700,
            currency="BRL",
            locale="pt-BR",
            market="BR",
            customer_email=u.email,
        ))
        db.commit()
        user_id = u.id
    finally:
        db.close()

    r = authed.get("/api/admin/sales")
    assert r.status_code == 200
    rows = r.json()["sales"]
    assert len(rows) == 1
    assert rows[0]["user_id"] == user_id
