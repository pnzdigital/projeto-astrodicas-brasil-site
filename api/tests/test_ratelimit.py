"""Rate limiting in-memory por IP: dentro do limite passa, acima retorna 429
com Retry-After, a janela expira e libera de novo, e o limite não é global
(outro IP não é afetado pelo consumo do primeiro)."""

from __future__ import annotations

import time

import pytest

from app import ratelimit


@pytest.fixture(autouse=True)
def _enable_rate_limit(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
    ratelimit.reset_all()
    yield
    ratelimit.reset_all()


def test_requests_within_limit_pass(monkeypatch, client):
    monkeypatch.setenv("RATE_LIMIT_AUTH_MAX", "3")
    monkeypatch.setenv("RATE_LIMIT_AUTH_WINDOW_SECONDS", "60")
    for _ in range(3):
        response = client.post("/api/auth/login", json={"email": "nao-existe@example.com", "password": "x"})
        assert response.status_code == 401  # senha errada, mas não é 429


def test_requests_above_limit_return_429_with_retry_after(monkeypatch, client):
    monkeypatch.setenv("RATE_LIMIT_AUTH_MAX", "3")
    monkeypatch.setenv("RATE_LIMIT_AUTH_WINDOW_SECONDS", "60")
    for _ in range(3):
        client.post("/api/auth/login", json={"email": "nao-existe@example.com", "password": "x"})
    blocked = client.post("/api/auth/login", json={"email": "nao-existe@example.com", "password": "x"})
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers
    assert int(blocked.headers["Retry-After"]) > 0


def test_window_expires_and_releases_again(monkeypatch, client):
    monkeypatch.setenv("RATE_LIMIT_AUTH_MAX", "2")
    monkeypatch.setenv("RATE_LIMIT_AUTH_WINDOW_SECONDS", "0.2")
    for _ in range(2):
        client.post("/api/auth/login", json={"email": "nao-existe@example.com", "password": "x"})
    blocked = client.post("/api/auth/login", json={"email": "nao-existe@example.com", "password": "x"})
    assert blocked.status_code == 429

    time.sleep(0.25)
    released = client.post("/api/auth/login", json={"email": "nao-existe@example.com", "password": "x"})
    assert released.status_code == 401


def test_limit_is_per_ip_not_global(monkeypatch, client):
    monkeypatch.setenv("RATE_LIMIT_AUTH_MAX", "2")
    monkeypatch.setenv("RATE_LIMIT_AUTH_WINDOW_SECONDS", "60")
    for _ in range(2):
        response = client.post(
            "/api/auth/login",
            json={"email": "nao-existe@example.com", "password": "x"},
            headers={"x-forwarded-for": "1.1.1.1"},
        )
        assert response.status_code == 401
    # TestClient sempre usa o mesmo request.client.host (testserver/testclient) por padrão;
    # forçamos IPs diferentes chamando a dependência diretamente para provar o isolamento por chave.
    from unittest.mock import MagicMock

    limiter = ratelimit.rate_limit(
        max_requests_env="RATE_LIMIT_AUTH_MAX",
        window_seconds_env="RATE_LIMIT_AUTH_WINDOW_SECONDS",
        default_max=2,
        default_window_seconds=60,
        key_fn=lambda request: "auth-isolation-test",
    )

    def fake_request(ip: str):
        request = MagicMock()
        request.client.host = ip
        request.url.path = "/api/auth/login"
        request.path_params = {}
        return request

    ip_a = fake_request("10.0.0.1")
    ip_b = fake_request("10.0.0.2")
    limiter(ip_a)
    limiter(ip_a)
    with pytest.raises(Exception):
        limiter(ip_a)  # terceiro request do mesmo IP estoura o limite
    limiter(ip_b)  # IP diferente começa do zero, não herda o consumo de A
    limiter(ip_b)


def test_disabled_rate_limit_never_blocks(monkeypatch, client):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "0")
    monkeypatch.setenv("RATE_LIMIT_AUTH_MAX", "1")
    for _ in range(5):
        response = client.post("/api/auth/login", json={"email": "nao-existe@example.com", "password": "x"})
        assert response.status_code == 401


def test_bypass_ip_is_never_limited(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_BYPASS_IPS", "10.0.0.9, 10.0.0.10")
    monkeypatch.setenv("RATE_LIMIT_AUTH_MAX", "1")
    monkeypatch.setenv("RATE_LIMIT_AUTH_WINDOW_SECONDS", "60")
    from unittest.mock import MagicMock

    limiter = ratelimit.rate_limit(
        max_requests_env="RATE_LIMIT_AUTH_MAX",
        window_seconds_env="RATE_LIMIT_AUTH_WINDOW_SECONDS",
        default_max=1,
        default_window_seconds=60,
    )
    request = MagicMock()
    request.client.host = "10.0.0.9"
    request.url.path = "/api/auth/login"
    for _ in range(10):
        limiter(request)  # nunca levanta, mesmo passando do limite


def test_checkout_order_and_webhook_have_independent_buckets(monkeypatch, client):
    monkeypatch.setenv("RATE_LIMIT_CHECKOUT_MAX", "1")
    monkeypatch.setenv("RATE_LIMIT_CHECKOUT_WINDOW_SECONDS", "60")
    monkeypatch.setenv("RATE_LIMIT_WEBHOOK_MAX", "1")
    monkeypatch.setenv("RATE_LIMIT_WEBHOOK_WINDOW_SECONDS", "60")

    first_order = client.post(
        "/api/checkout/order", json={"product_id": "site:mapa_astral", "email": "a@b.com", "locale": "pt-BR"}
    )
    assert first_order.status_code == 200
    second_order = client.post(
        "/api/checkout/order", json={"product_id": "site:mapa_astral", "email": "a@b.com", "locale": "pt-BR"}
    )
    assert second_order.status_code == 429

    # O bucket do checkout estourado não deve afetar o bucket do webhook (chaves diferentes).
    first_webhook = client.post(
        "/api/webhooks/cakto",
        json={"event_id": "evt-rl-1", "email": "a@b.com", "product_id": "site:plano_lua"},
    )
    assert first_webhook.status_code == 200
    second_webhook = client.post(
        "/api/webhooks/cakto",
        json={"event_id": "evt-rl-2", "email": "a@b.com", "product_id": "site:plano_lua"},
    )
    assert second_webhook.status_code == 429
    assert "Retry-After" in second_webhook.headers
