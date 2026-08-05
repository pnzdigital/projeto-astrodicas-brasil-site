"""Regression tests for the localized auth error responses.

Covers:
- Login 401 is identical for wrong-password vs unknown-email and picks
  pt-BR / es-AR from the body ``locale`` or ``Accept-Language``.
- The 401 returned by /api/me/* when no session is presented is
  localized.
- The rate-limiter 429 message is localized.

Register-specific localization/enumeration coverage was removed along with
``/api/auth/register`` (dash only supports login now; accounts are created
by purchase). Test users below are created directly in the DB via
``conftest.create_user``.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from conftest import create_user


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


def test_login_wrong_password_pt_br(fresh_client):
    create_user("ana@example.com", "senhaValida", name="Ana", locale="pt-BR")
    response = fresh_client.post(
        "/api/auth/login",
        json={"email": "ana@example.com", "password": "errada", "locale": "pt-BR"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "E-mail ou senha inválidos."


def test_login_wrong_password_es_ar(fresh_client):
    create_user("ana@example.com", "senhaValida", name="Ana", locale="es-AR")
    response = fresh_client.post(
        "/api/auth/login",
        json={"email": "ana@example.com", "password": "errada", "locale": "es-AR"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "E-mail o contraseña inválidos."


def test_login_unknown_email_es_ar_matches_wrong_password(fresh_client):
    """Um atacante não deve distinguir 'e-mail inexistente' de 'senha errada'."""
    create_user("ana@example.com", "senhaValida", name="Ana", locale="es-AR")
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
    create_user("ana@example.com", "senhaValida", name="Ana", locale="pt-BR")
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
    create_user("ana@example.com", "senhaValida", name="Ana")
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
