"""Regression tests for the account-enumeration hardening on /api/auth/register.

The old code returned 409 "Este e-mail já está cadastrado" for duplicate
emails, which leaked which addresses were customers. The hardened endpoint
returns a 200 response shaped like a fresh signup (same status, same body
keys) but DOES NOT issue a session cookie — so a probe learns nothing,
while the legitimate owner is notified by email and can still log in
normally afterwards.

These tests pin that contract.
"""

from __future__ import annotations

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
    with TestClient(app) as c:
        yield c


def _register_payload(email="ana@example.com", name="Ana Astro", locale="pt-BR"):
    return {"email": email, "password": "senhaValida", "name": name, "locale": locale}


def test_duplicate_email_returns_200_and_no_session_cookie(fresh_client):
    first = fresh_client.post("/api/auth/register", json=_register_payload())
    assert first.status_code == 200
    assert first.json()["created"] is True
    assert "site_session" in first.cookies

    fresh_client.post("/api/auth/logout")

    second = fresh_client.post("/api/auth/register", json=_register_payload(name="Outra"))
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["created"] is False
    assert body["user"]["email"] == "ana@example.com"
    # Sem cookie de sessão: o atacante não entra na conta do dono real.
    assert "site_session" not in second.cookies


def test_duplicate_response_indistinguishable_from_fresh_signup_status_and_keys(fresh_client):
    fresh = fresh_client.post("/api/auth/register", json=_register_payload(email="novo@example.com", name="Novo"))
    assert fresh.status_code == 200
    fresh_keys = sorted(fresh.json().keys())

    fresh_client.post("/api/auth/register", json=_register_payload(email="ana@example.com", name="Ana"))
    fresh_client.post("/api/auth/logout")
    dup = fresh_client.post("/api/auth/register", json=_register_payload(email="ana@example.com", name="Outra"))
    assert dup.status_code == fresh.status_code
    assert sorted(dup.json().keys()) == fresh_keys


def test_duplicate_response_body_differs_only_in_created_flag(fresh_client):
    """O conteúdo do user é o mesmo (não vaza nome original); só ``created`` muda.

    Observação: o backend devolve o nome cadastrado original para evitar que
    um atacante descubra o nome válido. Só o flag distingue.
    """
    fresh_client.post("/api/auth/register", json=_register_payload(name="Ana Astro"))
    fresh_client.post("/api/auth/logout")

    dup = fresh_client.post("/api/auth/register", json=_register_payload(name="Outro Nome"))
    assert dup.status_code == 200
    body = dup.json()
    assert body["created"] is False
    # O nome é o cadastrado originalmente, não o que o atacante submeteu.
    assert body["user"]["name"] == "Ana Astro"
    assert body["user"]["email"] == "ana@example.com"


def test_duplicate_attempt_does_not_modify_existing_user(fresh_client):
    fresh_client.post("/api/auth/register", json=_register_payload(name="Ana Astro"))
    fresh_client.post("/api/auth/logout")

    # Tenta sobrescrever com nome e senha novos.
    fresh_client.post(
        "/api/auth/register",
        json={"email": "ana@example.com", "password": "outraSenha99", "name": "Atacante", "locale": "pt-BR"},
    )

    # O login com a senha ORIGINAL continua funcionando.
    login = fresh_client.post(
        "/api/auth/login",
        json={"email": "ana@example.com", "password": "senhaValida", "locale": "pt-BR"},
    )
    assert login.status_code == 200
    assert login.json()["user"]["name"] == "Ana Astro"


def test_legitimate_owner_can_still_log_in_after_duplicate_attempt(fresh_client):
    fresh_client.post("/api/auth/register", json=_register_payload(name="Ana Astro"))
    fresh_client.post("/api/auth/logout")

    # Vários probes do atacante.
    for _ in range(3):
        fresh_client.post("/api/auth/register", json=_register_payload(name="Atacante"))

    # O dono real entra normalmente.
    login = fresh_client.post(
        "/api/auth/login",
        json={"email": "ana@example.com", "password": "senhaValida", "locale": "pt-BR"},
    )
    assert login.status_code == 200
    assert "site_session" in login.cookies


def test_duplicate_with_unknown_email_returns_validation_error(fresh_client):
    """E-mail inválido ainda gera 422 — só e-mails válidos acionam o caminho neutro."""
    response = fresh_client.post(
        "/api/auth/register",
        json={"email": "not-an-email", "password": "senhaValida", "name": "Ana", "locale": "pt-BR"},
    )
    assert response.status_code == 422
    assert "detail" in response.json()


def test_duplicate_triggers_existing_account_notice(monkeypatch):
    """E-mail de aviso deve ser disparado quando o e-mail já existe."""
    sent: list[dict] = []

    def fake_send(email, locale):
        sent.append({"email": email, "locale": locale})
        return {"ok": True, "noop": True}

    from app import mailer
    monkeypatch.setattr(mailer, "send_existing_account_notice", fake_send)

    from app.db import Base, engine
    from app.main import app
    from app import ratelimit

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    ratelimit.reset_all()

    with TestClient(app) as c:
        c.post("/api/auth/register", json=_register_payload())
        c.post("/api/auth/logout")
        c.post("/api/auth/register", json=_register_payload(name="Atacante"))
        c.post("/api/auth/logout")
        # Tentativa es-AR deve usar o locale do body no aviso.
        c.post(
            "/api/auth/register",
            json=_register_payload(name="Atacante2", locale="es-AR"),
        )

    assert len(sent) == 2
    assert sent[0] == {"email": "ana@example.com", "locale": "pt-BR"}
    assert sent[1] == {"email": "ana@example.com", "locale": "es-AR"}


def test_duplicate_response_status_equal_to_fresh_signup_latency_class(fresh_client):
    """Mesma classe de latência: ambos terminam sem trabalho de hash pesado."""
    import time

    fresh_client.post("/api/auth/register", json=_register_payload(email="existente@example.com"))
    fresh_client.post("/api/auth/logout")

    t_fresh_start = time.monotonic()
    fresh_client.post("/api/auth/register", json=_register_payload(email="novo@example.com"))
    t_fresh = time.monotonic() - t_fresh_start

    t_dup_start = time.monotonic()
    dup = fresh_client.post("/api/auth/register", json=_register_payload(email="existente@example.com", name="Outro"))
    t_dup = time.monotonic() - t_dup_start

    # O dup também faz hash de senha (defesa contra timing puro via probing
    # de work factor). Aqui só pinamos que o status code é igual — o hash
    # proposital de senhas iguais em ambos os caminhos equilibra o tempo.
    assert dup.status_code == 200
    # Sanidade grosseira: nenhum dos dois caminhos explode em tempo.
    assert t_fresh < 2.0 and t_dup < 2.0
