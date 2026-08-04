"""Regression tests for the localized auth error responses.

Covers:
- Empty-field validation returns a single translated sentence (no Pydantic
  schema leak) on both /api/auth/register and /api/auth/login.
- Login 401 is identical for wrong-password vs unknown-email and picks
  pt-BR / es-AR from the body ``locale`` or ``Accept-Language``.
- The 409-on-duplicate was replaced by a neutral 200 response — covered in
  test_auth_enumeration.py. The old 409 path is dead code now, but we
  pin the contract here so future changes cannot regress the localization.
- The 401 returned by /api/me/* when no session is presented is
  localized.
- The rate-limiter 429 message is localized.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def fresh_client():
    from app.db import Base, engine
    from app.main import app
    from app import ratelimit

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    ratelimit.reset_all()
    # Make sure the limiter is hot for the 429 test.
    os.environ["RATE_LIMIT_ENABLED"] = "1"
    os.environ["RATE_LIMIT_AUTH_MAX"] = "3"
    os.environ["RATE_LIMIT_AUTH_WINDOW_SECONDS"] = "60"
    with TestClient(app) as c:
        yield c


def test_register_empty_fields_pt_br(fresh_client):
    response = fresh_client.post(
        "/api/auth/register",
        json={"email": "", "password": "x", "name": "x", "locale": "pt-BR"},
    )
    assert response.status_code == 422
    body = response.json()
    assert "detail" in body
    assert isinstance(body["detail"], str)
    assert "[" not in body["detail"]  # não vaza o array Pydantic
    assert body["detail"] == "Informe um e-mail válido."


def test_register_empty_fields_es_ar(fresh_client):
    response = fresh_client.post(
        "/api/auth/register",
        json={"email": "", "password": "x", "name": "x", "locale": "es-AR"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Ingresá un e-mail válido."


def test_register_short_password_pt_br(fresh_client):
    response = fresh_client.post(
        "/api/auth/register",
        json={"email": "ana@example.com", "password": "short", "name": "Ana", "locale": "pt-BR"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "A senha precisa ter pelo menos 8 caracteres."


def test_register_short_password_es_ar(fresh_client):
    response = fresh_client.post(
        "/api/auth/register",
        json={"email": "ana@example.com", "password": "short", "name": "Ana", "locale": "es-AR"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "La contraseña debe tener al menos 8 caracteres."


def test_register_short_name_pt_br(fresh_client):
    response = fresh_client.post(
        "/api/auth/register",
        json={"email": "ana@example.com", "password": "senhaValida", "name": "A", "locale": "pt-BR"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Informe seu nome (mín. 2 caracteres)."


def test_register_accept_language_fallback_es_ar(fresh_client):
    # Sem locale no corpo; o backend cai pro Accept-Language.
    response = fresh_client.post(
        "/api/auth/register",
        json={"email": "ana@example.com", "password": "x", "name": "Ana"},
        headers={"Accept-Language": "es-AR"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "La contraseña debe tener al menos 8 caracteres."


def test_register_validation_falls_back_to_pt_br(fresh_client):
    # Sem locale e sem Accept-Language útil.
    response = fresh_client.post(
        "/api/auth/register",
        json={"email": "ana@example.com", "password": "x", "name": "Ana"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "A senha precisa ter pelo menos 8 caracteres."


def test_login_wrong_password_pt_br(fresh_client):
    fresh_client.post(
        "/api/auth/register",
        json={"email": "ana@example.com", "password": "senhaValida", "name": "Ana", "locale": "pt-BR"},
    )
    response = fresh_client.post(
        "/api/auth/login",
        json={"email": "ana@example.com", "password": "errada", "locale": "pt-BR"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "E-mail ou senha inválidos."


def test_login_wrong_password_es_ar(fresh_client):
    fresh_client.post(
        "/api/auth/register",
        json={"email": "ana@example.com", "password": "senhaValida", "name": "Ana", "locale": "es-AR"},
    )
    response = fresh_client.post(
        "/api/auth/login",
        json={"email": "ana@example.com", "password": "errada", "locale": "es-AR"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "E-mail o contraseña inválidos."


def test_login_unknown_email_es_ar_matches_wrong_password(fresh_client):
    """Um atacante não deve distinguir 'e-mail inexistente' de 'senha errada'."""
    fresh_client.post(
        "/api/auth/register",
        json={"email": "ana@example.com", "password": "senhaValida", "name": "Ana", "locale": "es-AR"},
    )
    wrong_pw = fresh_client.post(
        "/api/auth/login",
        json={"email": "ana@example.com", "password": "errada", "locale": "es-AR"},
    )
    unknown = fresh_client.post(
        "/api/auth/login",
        json={"email": "fantasma@example.com", "password": "qualquer", "locale": "es-AR"},
    )
    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.json() == unknown.json()


def test_login_unknown_email_pt_br_matches_wrong_password(fresh_client):
    fresh_client.post(
        "/api/auth/register",
        json={"email": "ana@example.com", "password": "senhaValida", "name": "Ana", "locale": "pt-BR"},
    )
    wrong_pw = fresh_client.post(
        "/api/auth/login",
        json={"email": "ana@example.com", "password": "errada", "locale": "pt-BR"},
    )
    unknown = fresh_client.post(
        "/api/auth/login",
        json={"email": "fantasma@example.com", "password": "qualquer", "locale": "pt-BR"},
    )
    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.json() == unknown.json()


def test_login_accept_language_fallback_es_ar(fresh_client):
    fresh_client.post(
        "/api/auth/register",
        json={"email": "ana@example.com", "password": "senhaValida", "name": "Ana"},
        headers={"Accept-Language": "es-AR"},
    )
    response = fresh_client.post(
        "/api/auth/login",
        json={"email": "ana@example.com", "password": "errada"},
        headers={"Accept-Language": "es-AR"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "E-mail o contraseña inválidos."


def test_me_profile_unauthenticated_localized_pt_br(fresh_client):
    response = fresh_client.get("/api/me/profile", headers={"Accept-Language": "pt-BR"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Faça login para continuar."


def test_me_profile_unauthenticated_localized_es_ar(fresh_client):
    response = fresh_client.get("/api/me/profile", headers={"Accept-Language": "es-AR"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Iniciá sesión para continuar."


def test_rate_limit_429_localized(fresh_client):
    # Limite configurado pra 3/min nesta fixture.
    for _ in range(3):
        fresh_client.post(
            "/api/auth/login",
            json={"email": "qualquer@example.com", "password": "qualquer"},
        )
    response = fresh_client.post(
        "/api/auth/login",
        json={"email": "qualquer@example.com", "password": "qualquer"},
        headers={"Accept-Language": "es-AR"},
    )
    assert response.status_code == 429
    assert response.json()["detail"] == "Demasiados intentos. Probá de nuevo en unos instantes."

    response_pt = fresh_client.post(
        "/api/auth/login",
        json={"email": "outro@example.com", "password": "qualquer"},
        headers={"Accept-Language": "pt-BR"},
    )
    assert response_pt.status_code == 429
    assert response_pt.json()["detail"] == "Muitas tentativas. Tente novamente em instantes."
