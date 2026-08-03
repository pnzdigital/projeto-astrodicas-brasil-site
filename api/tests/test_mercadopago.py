"""Adaptador Mercado Pago: assinatura, erros de rede e montagem de payload."""

import hashlib
import hmac
import urllib.error
from io import BytesIO
from unittest.mock import patch

import pytest

from app import mercadopago as mp


def test_access_token_missing_raises(monkeypatch):
    monkeypatch.delenv("MP_ACCESS_TOKEN", raising=False)
    with pytest.raises(mp.MercadoPagoError, match="MP_ACCESS_TOKEN"):
        mp.access_token()


def test_is_enabled_reflects_access_token(monkeypatch):
    monkeypatch.delenv("MP_ACCESS_TOKEN", raising=False)
    assert mp.is_enabled() is False
    monkeypatch.setenv("MP_ACCESS_TOKEN", "TEST-token")
    assert mp.is_enabled() is True


def test_public_key_reads_env(monkeypatch):
    monkeypatch.setenv("MP_PUBLIC_KEY", "APP_USR-xyz")
    assert mp.public_key() == "APP_USR-xyz"


class _FakeResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_request_success(monkeypatch):
    monkeypatch.setenv("MP_ACCESS_TOKEN", "TEST-token")
    with patch("app.mercadopago.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _FakeResponse(b'{"id": 1}')
        result = mp.get_payment("123")
    assert result == {"id": 1}


def test_request_http_error_becomes_mercadopago_error(monkeypatch):
    monkeypatch.setenv("MP_ACCESS_TOKEN", "TEST-token")
    error = urllib.error.HTTPError(
        "https://api.mercadopago.com/v1/payments/1", 400, "Bad Request", {}, BytesIO(b'{"message": "bad request"}')
    )
    with patch("app.mercadopago.urlopen", side_effect=error):
        with pytest.raises(mp.MercadoPagoError, match="400"):
            mp.get_payment("1")


def test_request_network_error_becomes_mercadopago_error(monkeypatch):
    monkeypatch.setenv("MP_ACCESS_TOKEN", "TEST-token")
    with patch("app.mercadopago.urlopen", side_effect=urllib.error.URLError("unreachable")):
        with pytest.raises(mp.MercadoPagoError, match="indisponível"):
            mp.get_payment("1")


def test_create_payment_includes_identification_and_notification_url(monkeypatch):
    monkeypatch.setenv("MP_ACCESS_TOKEN", "TEST-token")
    captured = {}

    def fake_request(method, path, payload=None, idempotency_key=""):
        captured["method"], captured["path"], captured["payload"] = method, path, payload
        return {"id": 1, "status": "approved"}

    monkeypatch.setattr(mp, "_request", fake_request)
    mp.create_payment(
        amount=97.0,
        description="Plano Lua Premium",
        order_id="order-1",
        payer_email="a@b.com",
        form_data={
            "payment_method_id": "visa",
            "token": "tok",
            "issuer_id": "25",
            "installments": 3,
            "payer": {"identification": {"type": "DNI", "number": "12345678"}, "first_name": "Ana"},
        },
        notification_url="https://astrodicas.example/api/webhooks/mercadopago/notify",
    )
    assert captured["payload"]["payer"]["identification"] == {"type": "DNI", "number": "12345678"}
    assert captured["payload"]["payer"]["first_name"] == "Ana"
    assert captured["payload"]["issuer_id"] == 25
    assert captured["payload"]["notification_url"] == "https://astrodicas.example/api/webhooks/mercadopago/notify"


def test_create_payment_drops_notification_url_for_localhost(monkeypatch):
    monkeypatch.setenv("MP_ACCESS_TOKEN", "TEST-token")
    captured = {}

    def fake_request(method, path, payload=None, idempotency_key=""):
        captured["payload"] = payload
        return {"id": 1}

    monkeypatch.setattr(mp, "_request", fake_request)
    mp.create_payment(
        amount=10.0,
        description="teste",
        order_id="order-2",
        payer_email="a@b.com",
        form_data={"payment_method_id": "visa"},
        notification_url="http://localhost:8000/api/webhooks/mercadopago/notify",
    )
    assert "notification_url" not in captured["payload"]


def test_internal_status_maps_known_and_unknown_statuses():
    assert mp.internal_status("approved") == "paid"
    assert mp.internal_status("rejected") == "failed"
    assert mp.internal_status(None) == "pending"
    assert mp.internal_status("something-new") == "pending"


def _signature_headers(secret: str, data_id: str, request_id: str, ts: str = "1700000000") -> tuple[str, str]:
    manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
    signature = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return f"ts={ts},v1={signature}", request_id


def test_verify_signature_accepts_valid_signature(monkeypatch):
    monkeypatch.setenv("MP_WEBHOOK_SECRET", "clave-secreta")
    header, request_id = _signature_headers("clave-secreta", "778899", "req-1")
    assert mp.verify_signature(header, request_id, "778899") is True


def test_verify_signature_rejects_tampered_signature(monkeypatch):
    monkeypatch.setenv("MP_WEBHOOK_SECRET", "clave-secreta")
    assert mp.verify_signature("ts=1700000000,v1=deadbeef", "req-1", "778899") is False


def test_verify_signature_rejects_malformed_header(monkeypatch):
    monkeypatch.setenv("MP_WEBHOOK_SECRET", "clave-secreta")
    assert mp.verify_signature("garbage-without-equals", "req-1", "778899") is False
    assert mp.verify_signature("ts=1700000000", "req-1", "778899") is False


def test_verify_signature_normalizes_data_id_case_per_mp_docs(monkeypatch):
    """Doc oficial: o manifest usa data.id em lowercase antes do HMAC."""
    monkeypatch.setenv("MP_WEBHOOK_SECRET", "clave-secreta")
    header, request_id = _signature_headers("clave-secreta", "abc123", "req-1")
    assert mp.verify_signature(header, request_id, "ABC123") is True


def test_verify_signature_replay_with_different_data_id_fails(monkeypatch):
    monkeypatch.setenv("MP_WEBHOOK_SECRET", "clave-secreta")
    header, request_id = _signature_headers("clave-secreta", "778899", "req-1")
    assert mp.verify_signature(header, request_id, "000000") is False
