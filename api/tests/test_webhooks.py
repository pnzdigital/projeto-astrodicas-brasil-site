"""Webhook signature validation hardening."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _cakto_payload(event_id: str = "evt-1") -> dict:
    return {
        "event_id": event_id,
        "email": "buyer@example.com",
        "product_id": "site:diario_astral",
        "status": "paid",
        "amount_minor": 1990,
        "currency": "BRL",
        "external_id": "ext-1",
    }


def _sign(secret: str, raw: bytes) -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


@pytest.fixture()
def webhook_client():
    with TestClient(app) as client:
        yield client


def test_cakto_empty_secret_production_returns_503(webhook_client, monkeypatch):
    monkeypatch.delenv("CAKTO_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("ALLOW_INSECURE_DEV", raising=False)
    response = webhook_client.post(
        "/api/webhooks/cakto",
        json=_cakto_payload("evt-prod"),
    )
    assert response.status_code == 503
    assert "segredo" in response.json()["detail"].lower()


def test_cakto_empty_secret_dev_without_allow_returns_503(webhook_client, monkeypatch):
    monkeypatch.delenv("CAKTO_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("ALLOW_INSECURE_DEV", raising=False)
    response = webhook_client.post(
        "/api/webhooks/cakto",
        json=_cakto_payload("evt-dev-noallow"),
    )
    assert response.status_code == 503


def test_cakto_empty_secret_dev_with_allow_accepted(webhook_client, monkeypatch):
    monkeypatch.delenv("CAKTO_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("ALLOW_INSECURE_DEV", "1")
    response = webhook_client.post(
        "/api/webhooks/cakto",
        json=_cakto_payload("evt-dev-allow"),
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_cakto_valid_signature_returns_200(webhook_client, monkeypatch):
    secret = "cakto-secret-32-bytes-minimum-1234"
    monkeypatch.setenv("CAKTO_WEBHOOK_SECRET", secret)
    payload = _cakto_payload("evt-valid")
    raw = json.dumps(payload).encode()
    headers = {"x-site-signature": _sign(secret, raw)}
    response = webhook_client.post(
        "/api/webhooks/cakto",
        content=raw,
        headers={**headers, "content-type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_cakto_invalid_signature_returns_401(webhook_client, monkeypatch):
    secret = "cakto-secret-32-bytes-minimum-1234"
    monkeypatch.setenv("CAKTO_WEBHOOK_SECRET", secret)
    payload = _cakto_payload("evt-bad")
    raw = json.dumps(payload).encode()
    headers = {
        "x-site-signature": "deadbeef" * 8,
        "content-type": "application/json",
    }
    response = webhook_client.post(
        "/api/webhooks/cakto",
        content=raw,
        headers=headers,
    )
    assert response.status_code == 401


def test_cakto_replay_is_deduped_with_duplicate_flag(webhook_client, monkeypatch):
    secret = "cakto-secret-32-bytes-minimum-1234"
    monkeypatch.setenv("CAKTO_WEBHOOK_SECRET", secret)
    payload = _cakto_payload("evt-replay")
    raw = json.dumps(payload).encode()
    headers = {
        "x-site-signature": _sign(secret, raw),
        "content-type": "application/json",
    }
    first = webhook_client.post("/api/webhooks/cakto", content=raw, headers=headers)
    second = webhook_client.post("/api/webhooks/cakto", content=raw, headers=headers)
    assert first.status_code == 200
    assert first.json()["ok"] is True
    assert second.status_code == 200
    assert second.json().get("duplicate") is True


def test_mercadopago_verify_signature_rejects_when_secret_empty_in_production(monkeypatch):
    from app import mercadopago
    monkeypatch.delenv("MP_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("ALLOW_INSECURE_DEV", raising=False)
    assert mercadopago.verify_signature("", "req-1", "12345") is False


def test_mercadopago_verify_signature_rejects_when_secret_empty_dev_no_allow(monkeypatch):
    from app import mercadopago
    monkeypatch.delenv("MP_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("ALLOW_INSECURE_DEV", raising=False)
    assert mercadopago.verify_signature("", "req-1", "12345") is False


def test_mercadopago_verify_signature_skips_when_dev_and_allow(monkeypatch):
    from app import mercadopago
    monkeypatch.delenv("MP_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("ALLOW_INSECURE_DEV", "1")
    assert mercadopago.verify_signature("", "req-1", "12345") is True


def test_mercadopago_via_provider_route_empty_secret_production_returns_503(webhook_client, monkeypatch):
    monkeypatch.delenv("MP_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("ALLOW_INSECURE_DEV", raising=False)
    response = webhook_client.post(
        "/api/webhooks/mercadopago",
        json=_cakto_payload("evt-mp-prod").update({}) or _cakto_payload("evt-mp-prod"),
    )
    assert response.status_code == 503


def test_mercadopago_via_provider_route_invalid_signature_returns_401(webhook_client, monkeypatch):
    secret = "mp-secret-32-bytes-minimum-1234567890"
    monkeypatch.setenv("MP_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("ALLOW_INSECURE_DEV", raising=False)
    payload = _cakto_payload("evt-mp-bad")
    raw = json.dumps(payload).encode()
    response = webhook_client.post(
        "/api/webhooks/mercadopago",
        content=raw,
        headers={"x-site-signature": "deadbeef" * 8, "content-type": "application/json"},
    )
    assert response.status_code == 401