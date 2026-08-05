import hashlib
import hmac
import json

import httpx
import pytest

from app.main import app
from app import engine
from conftest import register as _register


def register(client):
    response = _register(client, "ana@example.com", "senha-segura", name="Ana Astro")
    assert response.status_code == 200, response.text
    return response


def save_birth_profile(client):
    response = client.put(
        "/api/me/profile",
        json={
            "birth_date": "1990-05-20",
            "birth_time": "12:30:00",
            "birth_city": "Recife",
            "birth_country": "BR",
        },
    )
    assert response.status_code == 200, response.text
    return response


def test_auth_and_profile_round_trip(client):
    registered = register(client)
    assert registered.json()["user"]["email"] == "ana@example.com"
    assert client.get("/api/session").json()["authenticated"] is True

    save_birth_profile(client)
    profile = client.get("/api/me/profile")
    assert profile.status_code == 200
    assert profile.json()["profile"]["birth_city"] == "Recife"

    client.post("/api/auth/logout")
    assert client.get("/api/session").json() == {"authenticated": False}


def test_webhook_is_verified_idempotent_and_grants_access(client, monkeypatch):
    register(client)
    client.post("/api/auth/logout")
    payload = {
        "event_id": "evt-1",
        "email": "ana@example.com",
        "product_id": "site:plano_lua",
        "external_id": "order-1",
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    secret = "webhook-test-secret"
    monkeypatch.setenv("CAKTO_WEBHOOK_SECRET", secret)
    headers = {"x-site-signature": hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()}

    first = client.post("/api/webhooks/cakto", content=raw, headers=headers)
    assert first.status_code == 200, first.text
    duplicate = client.post("/api/webhooks/cakto", content=raw, headers=headers)
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True


def test_generate_requires_profile_then_returns_ready_reading(client):
    register(client)
    blocked = client.post("/api/me/readings/site:content:horoscopo_diario/generate")
    assert blocked.status_code == 422
    save_birth_profile(client)

    # This content is paid, so the entitlement must be granted by the site webhook.
    payload = {
        "event_id": "evt-generate",
        "email": "ana@example.com",
        "product_id": "site:plano_lua",
    }
    granted = client.post("/api/webhooks/cakto", json=payload)
    assert granted.status_code == 200, granted.text

    generated = client.post("/api/me/readings/site:content:horoscopo_diario/generate")
    assert generated.status_code == 200, generated.text
    reading = generated.json()["reading"]
    # In the test env MINIMAX_API_KEY is unset, so the engine returns the
    # editorial fallback. The new contract surfaces this honestly via
    # status='fallback' + source='fallback' + warning, instead of silently
    # presenting the generic text as the paid personalized reading.
    assert reading["status"] == "fallback"
    assert reading["source"] == "fallback"
    assert reading["warning"]
    assert "Touro" in reading["body_html"]

    same = client.post("/api/me/readings/site:content:horoscopo_diario/generate")
    assert same.json()["reading"]["id"] == reading["id"]


@pytest.mark.asyncio
async def test_webhook_async_client_accepts_json_body():
    # ASGITransport exercises the async endpoint and Request.body() path directly.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/webhooks/unknown",
            json={"event_id": "evt", "email": "ana@example.com", "product_id": "site:x"},
        )
    assert response.status_code == 404


def test_minimax_output_is_escaped_and_formatted(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    monkeypatch.setattr(engine, "_call_minimax", lambda prompt, locale="pt-BR": "Primeiro & direto.\n\nSegundo <seguro>.\n\nTerceiro caminho.")

    result = engine.generate_reading("site:content:horoscopo_diario", "Horóscopo diário", None)

    assert result.body_html.count("<p>") == 3
    assert "Primeiro &amp; direto." in result.body_html
    assert "Segundo &lt;seguro&gt;." in result.body_html
    assert result.source == "minimax"
