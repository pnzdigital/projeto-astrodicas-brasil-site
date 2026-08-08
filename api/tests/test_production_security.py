"""Prontidão de produção: o app nunca deve subir com segredo crítico ausente,
e ALLOW_INSECURE_DEV/ALLOW_DEMO nunca podem furar essa trava em produção.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app, validate_production_config


@pytest.fixture()
def production_env(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("MP_WEBHOOK_SECRET", "mp-secret-32-bytes-minimum-1234567890")
    monkeypatch.setenv("CAKTO_WEBHOOK_SECRET", "cakto-secret-32-bytes-minimum-1234")
    monkeypatch.setenv("ADMIN_PASSWORD", "senha-forte-de-producao")
    monkeypatch.setenv("COOKIE_SECURE", "1")
    return None


def test_validate_production_config_passes_when_fully_configured(production_env):
    validate_production_config()  # não deve levantar


@pytest.mark.parametrize("missing", ["MP_WEBHOOK_SECRET", "CAKTO_WEBHOOK_SECRET", "ADMIN_PASSWORD"])
def test_validate_production_config_fails_when_secret_missing(production_env, monkeypatch, missing):
    monkeypatch.delenv(missing, raising=False)
    with pytest.raises(RuntimeError, match=missing):
        validate_production_config()


def test_validate_production_config_fails_when_cookie_not_secure(production_env, monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "0")
    with pytest.raises(RuntimeError, match="COOKIE_SECURE"):
        validate_production_config()


def test_validate_production_config_ignores_allow_insecure_dev_in_production(production_env, monkeypatch):
    """ALLOW_INSECURE_DEV=1 setado por engano em produção não pode furar a trava."""
    monkeypatch.delenv("MP_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("ALLOW_INSECURE_DEV", "1")
    with pytest.raises(RuntimeError, match="MP_WEBHOOK_SECRET"):
        validate_production_config()


def test_validate_production_config_noop_outside_production(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("MP_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("CAKTO_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    validate_production_config()  # não deve levantar fora de produção


def test_app_refuses_to_boot_in_production_without_secrets(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("MP_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("CAKTO_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass


def test_webhook_route_still_rejects_in_production_even_with_allow_insecure_dev(production_env, monkeypatch):
    """Belt-and-suspenders: além da trava de startup, a rota em si também recusa."""
    with TestClient(app) as client:
        # Startup já validou com os segredos completos; agora simulamos um operador
        # que ligou ALLOW_INSECURE_DEV por engano e removeu o segredo do cakto em runtime.
        monkeypatch.delenv("CAKTO_WEBHOOK_SECRET", raising=False)
        monkeypatch.setenv("ALLOW_INSECURE_DEV", "1")
        response = client.post(
            "/api/webhooks/cakto",
            json={"event_id": "evt-prod-check", "email": "a@b.com", "product_id": "site:diario_astral"},
        )
        assert response.status_code == 503
