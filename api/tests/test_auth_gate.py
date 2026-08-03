"""Auth gate: demo content must be locked behind a valid session.

Sem session cookie válida, o portal mostra a tela de login. O modo
`?demo=paid` só libera vitrine de demonstração quando o backend
autoriza via /api/auth-gate — o que exige ALLOW_DEMO=1 com
ENV != production. Em produção, o gate sempre rejeita mesmo
com ALLOW_DEMO=1 setado.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_DIR.parents[1]))


def _reload_main(monkeypatch):
    """Reload app.main with the requested env vars."""
    sys.modules.pop("app.main", None)
    return importlib.import_module("app.main")


@pytest.fixture
def dev_client(monkeypatch):
    monkeypatch.setenv("SITE_SECRET_KEY", "x" * 32)
    monkeypatch.setenv("COOKIE_SECURE", "0")
    monkeypatch.setenv("SITE_ORIGIN", "http://testserver")
    monkeypatch.setenv("GEOCODING_ENABLED", "0")
    monkeypatch.setenv("ENV", "development")
    main = _reload_main(monkeypatch)
    with TestClient(main.app) as client:
        yield client


@pytest.fixture
def dev_client_with_demo(monkeypatch):
    monkeypatch.setenv("SITE_SECRET_KEY", "x" * 32)
    monkeypatch.setenv("COOKIE_SECURE", "0")
    monkeypatch.setenv("SITE_ORIGIN", "http://testserver")
    monkeypatch.setenv("GEOCODING_ENABLED", "0")
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("ALLOW_DEMO", "1")
    main = _reload_main(monkeypatch)
    with TestClient(main.app) as client:
        yield client


def _set_production_secrets(monkeypatch):
    """validate_production_config() no startup exige estes segredos em ENV=production."""
    monkeypatch.setenv("MP_WEBHOOK_SECRET", "mp-secret-32-bytes-minimum-1234567890")
    monkeypatch.setenv("CAKTO_WEBHOOK_SECRET", "cakto-secret-32-bytes-minimum-1234")
    monkeypatch.setenv("ADMIN_PASSWORD", "senha-forte-de-producao")
    monkeypatch.setenv("COOKIE_SECURE", "1")


@pytest.fixture
def prod_client_with_demo(monkeypatch):
    monkeypatch.setenv("SITE_SECRET_KEY", "x" * 32)
    monkeypatch.setenv("SITE_ORIGIN", "http://testserver")
    monkeypatch.setenv("GEOCODING_ENABLED", "0")
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("ALLOW_DEMO", "1")
    _set_production_secrets(monkeypatch)
    main = _reload_main(monkeypatch)
    with TestClient(main.app) as client:
        yield client


def test_session_without_cookie_is_unauthenticated(dev_client):
    response = dev_client.get("/api/session")
    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is False
    assert "user" not in body or body.get("user") in (None,)


def test_auth_gate_without_demo_param_returns_false(dev_client_with_demo):
    response = dev_client_with_demo.get("/api/auth-gate")
    assert response.status_code == 200
    assert response.json() == {"demo": False}


def test_auth_gate_demo_paid_without_env_flag_is_403(dev_client):
    response = dev_client.get("/api/auth-gate", params={"demo": "paid"})
    assert response.status_code == 403
    assert "indisponível" in response.json().get("detail", "").lower()


def test_auth_gate_demo_paid_with_env_dev_returns_200_with_banner(dev_client_with_demo):
    response = dev_client_with_demo.get("/api/auth-gate", params={"demo": "paid"})
    assert response.status_code == 200
    body = response.json()
    assert body == {"demo": True}


def test_auth_gate_demo_paid_in_production_still_403(prod_client_with_demo):
    response = prod_client_with_demo.get("/api/auth-gate", params={"demo": "paid"})
    assert response.status_code == 403


def test_auth_gate_demo_paid_in_production_without_env_still_403(monkeypatch):
    monkeypatch.setenv("SITE_SECRET_KEY", "x" * 32)
    monkeypatch.setenv("SITE_ORIGIN", "http://testserver")
    monkeypatch.setenv("GEOCODING_ENABLED", "0")
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("ALLOW_DEMO", raising=False)
    _set_production_secrets(monkeypatch)
    main = _reload_main(monkeypatch)
    with TestClient(main.app) as client:
        response = client.get("/api/auth-gate", params={"demo": "paid"})
    assert response.status_code == 403


def test_session_rejects_invalid_cookie(dev_client_with_demo):
    response = dev_client_with_demo.get(
        "/api/session",
        cookies={"site_session": "not-a-real-token"},
    )
    assert response.status_code == 200
    assert response.json()["authenticated"] is False
