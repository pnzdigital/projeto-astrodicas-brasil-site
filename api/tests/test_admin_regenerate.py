"""Testes TDD para as rotas de suporte de leituras no admin."""

import os
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.admin import router as admin_router
from app.db import SessionLocal
from app.models import GenerationJob, Reading, User
from app.security import hash_password

os.environ["ADMIN_PASSWORD"] = "letmein-test"


@pytest.fixture()
def admin_client(clean_database):
    app = FastAPI()
    app.include_router(admin_router)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def authed_admin(admin_client):
    """Client já logado como admin."""
    r = admin_client.post("/api/admin/login", json={"password": "letmein-test"})
    assert r.status_code == 200, r.text
    return admin_client


def _seed_user_with_reading(content_id="site:content:mapa_astral_completo", source="fallback", status="fallback"):
    db = SessionLocal()
    try:
        user = User(
            id=str(uuid4()),
            email="cliente@example.com",
            password_hash=hash_password("senha"),
            name="Cliente Teste",
            locale="pt-BR",
        )
        db.add(user)
        db.flush()
        reading = Reading(
            id=str(uuid4()),
            user_id=user.id,
            content_id=content_id,
            status=status,
            source=source,
            title="Mapa Astral Completo",
            sections_total=15,
            sections_done=15,
        )
        db.add(reading)
        db.commit()
        db.refresh(user)
        db.refresh(reading)
        return user.id, reading.id, user.email
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Segurança: sem sessão → 401
# ---------------------------------------------------------------------------

def test_users_search_requires_auth(admin_client):
    r = admin_client.get("/api/admin/users/search", params={"email": "x@x.com"})
    assert r.status_code == 401


def test_user_readings_requires_auth(admin_client):
    r = admin_client.get("/api/admin/users/some-id/readings")
    assert r.status_code == 401


def test_regenerate_requires_auth(admin_client):
    r = admin_client.post("/api/admin/readings/some-id/regenerate")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Busca de usuário
# ---------------------------------------------------------------------------

def test_search_user_found(authed_admin):
    _seed_user_with_reading()
    r = authed_admin.get("/api/admin/users/search", params={"email": "cliente@example.com"})
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "cliente@example.com"
    assert body["name"] == "Cliente Teste"
    assert "id" in body


def test_search_user_case_insensitive(authed_admin):
    _seed_user_with_reading()
    r = authed_admin.get("/api/admin/users/search", params={"email": "CLIENTE@EXAMPLE.COM"})
    assert r.status_code == 200


def test_search_user_not_found(authed_admin):
    r = authed_admin.get("/api/admin/users/search", params={"email": "naoexiste@example.com"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Listagem de leituras
# ---------------------------------------------------------------------------

def test_list_readings_for_user(authed_admin):
    user_id, reading_id, _ = _seed_user_with_reading(source="fallback", status="fallback")
    r = authed_admin.get(f"/api/admin/users/{user_id}/readings")
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == user_id
    readings = body["readings"]
    assert len(readings) == 1
    row = readings[0]
    assert row["id"] == reading_id
    assert row["is_fallback"] is True
    assert row["estimated_requests"] > 0


def test_list_readings_marks_fallback_correctly(authed_admin):
    user_id, _, _ = _seed_user_with_reading(source="minimax", status="ready")
    r = authed_admin.get(f"/api/admin/users/{user_id}/readings")
    row = r.json()["readings"][0]
    assert row["is_fallback"] is False


def test_list_readings_user_not_found(authed_admin):
    r = authed_admin.get("/api/admin/users/nao-existe/readings")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Caminho feliz: regerar leitura
# ---------------------------------------------------------------------------

def test_regenerate_happy_path(authed_admin, skip_auto_run):
    user_id, reading_id, _ = _seed_user_with_reading(source="fallback", status="fallback")

    r = authed_admin.post(f"/api/admin/readings/{reading_id}/regenerate")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["ok"] is True
    assert body["superseded_reading_id"] == reading_id
    new_id = body["new_reading_id"]
    assert new_id != reading_id
    assert body["estimated_requests"] >= 1

    db = SessionLocal()
    try:
        old = db.get(Reading, reading_id)
        assert old.status == "superseded"

        new = db.get(Reading, new_id)
        assert new.status == "pending"
        assert new.content_id == old.content_id

        job = db.get(GenerationJob, body["job_id"])
        assert job is not None
        assert job.reading_id == new_id
    finally:
        db.close()


def test_regenerate_not_found(authed_admin):
    r = authed_admin.post("/api/admin/readings/nao-existe/regenerate")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Proteção contra duplo clique
# ---------------------------------------------------------------------------

def test_regenerate_blocks_when_job_active(authed_admin, skip_auto_run):
    user_id, reading_id, _ = _seed_user_with_reading(source="fallback", status="fallback")

    # Primeira regeração — cria job
    r1 = authed_admin.post(f"/api/admin/readings/{reading_id}/regenerate")
    assert r1.status_code == 200

    new_reading_id = r1.json()["new_reading_id"]

    # Segunda tentativa antes do job terminar — deve ser bloqueada
    r2 = authed_admin.post(f"/api/admin/readings/{new_reading_id}/regenerate")
    assert r2.status_code == 409, r2.text
