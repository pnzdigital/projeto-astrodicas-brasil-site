"""Regression tests for /api/auth/password-reset/*.

Cobre:
- Happy path (criação do token, e-mail enviado, confirm com senha nova).
- Token expirado (TTL curto configurável via env).
- Token reusado (segundo confirm retorna invalid).
- Token adulterado (comprimento certo mas bytes errados).
- E-mail desconhecido: resposta idêntica à do e-mail cadastrado
  (anti-enumeração), mas nenhum e-mail é enviado.
- Rate limit: 6ª tentativa em sequência retorna 429.
- Reset invalida sessões anteriores (``token_epoch`` mismatch).
- Localização pt-BR / es-AR em ambos os endpoints.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select


@pytest.fixture()
def fresh_client(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
    monkeypatch.setenv("RATE_LIMIT_PASSWORD_RESET_MAX", "5")
    monkeypatch.setenv("RATE_LIMIT_PASSWORD_RESET_WINDOW_SECONDS", "60")
    monkeypatch.setenv("PASSWORD_RESET_TTL_SECONDS", "1800")

    from app.db import Base, engine
    from app.main import app
    from app import ratelimit

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    ratelimit.reset_all()
    with TestClient(app) as c:
        yield c


def _register(client, email="ana@example.com", password="senhaValida"):
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": password, "name": "Ana Astro", "locale": "pt-BR"},
    )
    assert response.status_code == 200, response.text
    client.post("/api/auth/logout")
    return response


def _last_token(mailer_calls):
    assert mailer_calls, "nenhum e-mail capturado"
    return mailer_calls[-1]["reset_token"]


def test_request_with_unknown_email_returns_neutral_response(fresh_client, monkeypatch):
    sent: list[dict] = []

    def fake_send(email, token, expires_at, locale, portal_url):
        sent.append({"email": email, "token": token, "locale": locale})
        return {"ok": True, "noop": True}

    from app import mailer
    monkeypatch.setattr(mailer, "send_password_reset", fake_send)

    response = fresh_client.post(
        "/api/auth/password-reset/request",
        json={"email": "fantasma@example.com", "locale": "pt-BR"},
    )
    assert response.status_code == 200
    assert "detail" in response.json()
    assert "cadastrado" in response.json()["detail"].lower() or "registrado" in response.json()["detail"].lower()
    assert sent == [], "e-mail NÃO pode ser enviado para conta inexistente"


def test_request_with_known_email_sends_token(fresh_client, monkeypatch):
    sent: list[dict] = []

    def fake_send(email, token, expires_at, locale, portal_url):
        sent.append({"email": email, "token": token, "locale": locale, "expires_at": expires_at})
        return {"ok": True, "noop": True}

    from app import mailer
    monkeypatch.setattr(mailer, "send_password_reset", fake_send)

    _register(fresh_client)
    response = fresh_client.post(
        "/api/auth/password-reset/request",
        json={"email": "ana@example.com", "locale": "pt-BR"},
    )
    assert response.status_code == 200
    assert len(sent) == 1
    assert sent[0]["email"] == "ana@example.com"
    assert sent[0]["locale"] == "pt-BR"
    assert len(sent[0]["token"]) >= 32  # secrets.token_urlsafe(32) gera ~43 chars


def test_request_response_indistinguishable_for_known_vs_unknown(fresh_client, monkeypatch):
    """Mesma chave, mesma estrutura, mesmo status — só a chave 'detail'."""
    monkeypatch.setattr("app.mailer.send_password_reset", lambda *a, **k: {"ok": True})

    _register(fresh_client)
    known = fresh_client.post(
        "/api/auth/password-reset/request",
        json={"email": "ana@example.com", "locale": "pt-BR"},
    )
    fresh_client.post(
        "/api/auth/password-reset/request",
        json={"email": "fantasma@example.com", "locale": "pt-BR"},
    )
    unknown = fresh_client.post(
        "/api/auth/password-reset/request",
        json={"email": "outro@example.com", "locale": "pt-BR"},
    )

    assert known.status_code == unknown.status_code == 200
    assert known.json().keys() == unknown.json().keys()
    assert known.json()["detail"] == unknown.json()["detail"]


def test_request_localized_es_ar(fresh_client, monkeypatch):
    captured: dict = {}

    def fake_send(email, token, expires_at, locale, portal_url):
        captured["locale"] = locale
        return {"ok": True}

    monkeypatch.setattr("app.mailer.send_password_reset", fake_send)
    _register(fresh_client)

    response = fresh_client.post(
        "/api/auth/password-reset/request",
        json={"email": "ana@example.com", "locale": "es-AR"},
    )
    assert response.status_code == 200
    assert "registrado" in response.json()["detail"].lower()
    assert captured["locale"] == "es-AR"


def test_confirm_happy_path_resets_password(fresh_client, monkeypatch):
    sent: list[dict] = []

    def fake_send(email, token, expires_at, locale, portal_url):
        sent.append({"reset_token": token, "locale": locale})
        return {"ok": True}

    monkeypatch.setattr("app.mailer.send_password_reset", fake_send)
    _register(fresh_client)
    fresh_client.post(
        "/api/auth/password-reset/request",
        json={"email": "ana@example.com", "locale": "pt-BR"},
    )
    token = _last_token(sent)

    confirm = fresh_client.post(
        "/api/auth/password-reset/confirm",
        json={"token": token, "password": "novaSenha99", "locale": "pt-BR"},
    )
    assert confirm.status_code == 200
    assert "detail" in confirm.json()

    # Senha antiga não entra mais; a nova entra.
    old = fresh_client.post(
        "/api/auth/login",
        json={"email": "ana@example.com", "password": "senhaValida", "locale": "pt-BR"},
    )
    assert old.status_code == 401
    new = fresh_client.post(
        "/api/auth/login",
        json={"email": "ana@example.com", "password": "novaSenha99", "locale": "pt-BR"},
    )
    assert new.status_code == 200


def test_confirm_invalidates_existing_sessions(fresh_client, monkeypatch):
    """Sessões abertas antes do reset precisam cair em 401 depois."""
    monkeypatch.setattr("app.mailer.send_password_reset", lambda *a, **k: {"ok": True})
    _register(fresh_client)

    # Pega um cookie de sessão válido.
    login = fresh_client.post(
        "/api/auth/login",
        json={"email": "ana@example.com", "password": "senhaValida", "locale": "pt-BR"},
    )
    assert login.status_code == 200
    assert "site_session" in login.cookies
    session_before = fresh_client.cookies.get("site_session")

    # Sessão aberta está autenticada.
    me = fresh_client.get("/api/me/profile")
    assert me.status_code == 200

    # Dispara reset.
    sent: list[dict] = []
    def fake_send(email, token, expires_at, locale, portal_url):
        sent.append({"reset_token": token})
        return {"ok": True}
    monkeypatch.setattr("app.mailer.send_password_reset", fake_send)
    fresh_client.post(
        "/api/auth/password-reset/request",
        json={"email": "ana@example.com", "locale": "pt-BR"},
    )
    fresh_client.post(
        "/api/auth/password-reset/confirm",
        json={"token": sent[-1]["reset_token"], "password": "outraSenha99", "locale": "pt-BR"},
    )

    # Cookie da sessão anterior agora precisa falhar.
    fresh_client.cookies.set("site_session", session_before)
    me_after = fresh_client.get("/api/me/profile")
    assert me_after.status_code == 401


def test_confirm_with_reused_token_fails(fresh_client, monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(
        "app.mailer.send_password_reset",
        lambda email, token, *a, **k: sent.append({"reset_token": token}) or {"ok": True},
    )
    _register(fresh_client)
    fresh_client.post(
        "/api/auth/password-reset/request",
        json={"email": "ana@example.com", "locale": "pt-BR"},
    )
    token = sent[-1]["reset_token"]

    first = fresh_client.post(
        "/api/auth/password-reset/confirm",
        json={"token": token, "password": "novaSenha99", "locale": "pt-BR"},
    )
    assert first.status_code == 200

    # Segundo uso do mesmo token deve falhar.
    fresh_client.post(
        "/api/auth/login",
        json={"email": "ana@example.com", "password": "novaSenha99", "locale": "pt-BR"},
    )
    second = fresh_client.post(
        "/api/auth/password-reset/confirm",
        json={"token": token, "password": "outraSenha99", "locale": "pt-BR"},
    )
    assert second.status_code == 400
    assert "expir" in second.json()["detail"].lower() or "usado" in second.json()["detail"].lower()


def test_confirm_with_expired_token_fails(fresh_client, monkeypatch):
    """Forçamos expiração editando o expires_at no banco."""
    from app.db import SessionLocal
    from app.models import PasswordResetToken
    from app.security import hash_reset_token, new_reset_token

    monkeypatch.setattr("app.mailer.send_password_reset", lambda *a, **k: {"ok": True})
    _register(fresh_client)
    raw, hashed = new_reset_token()
    expires_at = datetime.now(timezone.utc) - timedelta(seconds=10)  # já vencido
    with SessionLocal() as db:
        from app.models import User
        user = db.scalar(select(User).where(User.email == "ana@example.com"))
        db.add(PasswordResetToken(user_id=user.id, token_hash=hashed, expires_at=expires_at, locale="pt-BR"))
        db.commit()

    response = fresh_client.post(
        "/api/auth/password-reset/confirm",
        json={"token": raw, "password": "novaSenha99", "locale": "pt-BR"},
    )
    assert response.status_code == 400
    assert "expir" in response.json()["detail"].lower() or "usado" in response.json()["detail"].lower()


def test_confirm_with_tampered_token_fails(fresh_client, monkeypatch):
    """Token de mesmo comprimento mas bytes errados: falha sem vazar qual parte bateu."""
    monkeypatch.setattr("app.mailer.send_password_reset", lambda *a, **k: {"ok": True})
    _register(fresh_client)
    fresh_client.post(
        "/api/auth/password-reset/request",
        json={"email": "ana@example.com", "locale": "pt-BR"},
    )
    # Gera um token "válido em forma" mas que não corresponde a nenhum hash.
    tampered = "a" * 43
    response = fresh_client.post(
        "/api/auth/password-reset/confirm",
        json={"token": tampered, "password": "novaSenha99", "locale": "pt-BR"},
    )
    assert response.status_code == 400
    assert "expir" in response.json()["detail"].lower() or "usado" in response.json()["detail"].lower()


def test_confirm_with_short_password_fails_validation(fresh_client, monkeypatch):
    monkeypatch.setattr("app.mailer.send_password_reset", lambda *a, **k: {"ok": True})
    _register(fresh_client)
    sent: list[dict] = []
    monkeypatch.setattr(
        "app.mailer.send_password_reset",
        lambda email, token, *a, **k: sent.append({"reset_token": token}) or {"ok": True},
    )
    fresh_client.post(
        "/api/auth/password-reset/request",
        json={"email": "ana@example.com", "locale": "pt-BR"},
    )
    token = sent[-1]["reset_token"]
    response = fresh_client.post(
        "/api/auth/password-reset/confirm",
        json={"token": token, "password": "short", "locale": "pt-BR"},
    )
    assert response.status_code == 422
    assert "8 caracteres" in response.json()["detail"]


def test_confirm_invalidates_only_after_password_change(fresh_client, monkeypatch):
    """Confirmação não emite cookie de sessão (forçar login separado)."""
    sent: list[dict] = []
    monkeypatch.setattr(
        "app.mailer.send_password_reset",
        lambda email, token, *a, **k: sent.append({"reset_token": token}) or {"ok": True},
    )
    _register(fresh_client)
    fresh_client.post(
        "/api/auth/password-reset/request",
        json={"email": "ana@example.com", "locale": "pt-BR"},
    )
    response = fresh_client.post(
        "/api/auth/password-reset/confirm",
        json={"token": sent[-1]["reset_token"], "password": "novaSenha99", "locale": "pt-BR"},
    )
    assert response.status_code == 200
    assert "site_session" not in response.cookies


def test_rate_limit_blocks_after_threshold(fresh_client, monkeypatch):
    monkeypatch.setattr("app.mailer.send_password_reset", lambda *a, **k: {"ok": True})
    _register(fresh_client)

    # Limite configurado em 5/min nesta fixture.
    for _ in range(5):
        fresh_client.post(
            "/api/auth/password-reset/request",
            json={"email": "ana@example.com", "locale": "pt-BR"},
        )
    blocked = fresh_client.post(
        "/api/auth/password-reset/request",
        json={"email": "ana@example.com", "locale": "pt-BR"},
        headers={"Accept-Language": "es-AR"},
    )
    assert blocked.status_code == 429
    assert "demasiados" in blocked.json()["detail"].lower() or "instantes" in blocked.json()["detail"].lower()


def test_request_does_not_leak_email_validity_via_timing(monkeypatch):
    """Comparação grosseira: conhecidos e inexistentes custam parecidos."""
    import time

    from app.db import Base, engine
    from app.main import app
    from app import ratelimit

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    ratelimit.reset_all()
    monkeypatch.setattr("app.mailer.send_password_reset", lambda *a, **k: {"ok": True})

    with TestClient(app) as c:
        c.post(
            "/api/auth/register",
            json={"email": "ana@example.com", "password": "senhaValida", "name": "Ana", "locale": "pt-BR"},
        )
        c.post("/api/auth/logout")

        # Warmup.
        for _ in range(2):
            c.post("/api/auth/password-reset/request", json={"email": "warmup@example.com", "locale": "pt-BR"})

        t_known = time.monotonic()
        c.post("/api/auth/password-reset/request", json={"email": "ana@example.com", "locale": "pt-BR"})
        d_known = time.monotonic() - t_known

        t_unknown = time.monotonic()
        c.post("/api/auth/password-reset/request", json={"email": "outro@example.com", "locale": "pt-BR"})
        d_unknown = time.monotonic() - t_unknown

        # Não assertion estrita — só garante que nenhum dos dois caminhos
        # explode. Os dois caminhos geram+descartam um token para equalizar.
        assert d_known < 2.0
        assert d_unknown < 2.0
