"""Quem paga tem que conseguir entrar.

A conta do comprador nasce dentro do webhook, com uma senha que só o servidor
conhece. Enquanto essa senha não saía de lá, o cliente pagava e ficava do lado
de fora: não sabia a senha, e o "esqueci a senha" manda justamente o e-mail que
ninguém estava mandando. O prejuízo é silencioso — a compra consta como
aprovada, o acesso consta como liberado, e é o cliente que descobre.

Dois fatos sustentam a rota, e são estes que os testes travam:

1. o e-mail sai com a senha temporária, e a senha realmente abre a conta;
2. falha no provedor de e-mail não desfaz nem bloqueia a compra — o acesso é o
   que a pessoa pagou.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app


def _payload(event_id: str, email: str = "compradora@example.com") -> dict:
    return {
        "event_id": event_id,
        "email": email,
        "product_id": "site:mapa_astral",
        "status": "paid",
        "amount_minor": 4700,
        "currency": "BRL",
        "external_id": f"ext-{event_id}",
    }


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def outbox(monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(
        main,
        "send_purchase_confirmation",
        lambda **kwargs: sent.append(kwargs) or {"sent": True, "id": "email-1"},
    )
    return sent


def _post(client, secret: str, payload: dict):
    raw = json.dumps(payload).encode()
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        "/api/webhooks/cakto",
        content=raw,
        headers={"x-site-signature": signature, "content-type": "application/json"},
    )


def test_compra_manda_email_e_a_senha_temporaria_abre_a_conta(client, outbox, monkeypatch):
    secret = "s" * 32
    monkeypatch.setenv("CAKTO_WEBHOOK_SECRET", secret)

    response = _post(client, secret, _payload("evt-email-1"))
    assert response.status_code == 200

    assert len(outbox) == 1, "compra confirmada sem e-mail é cliente trancado do lado de fora"
    enviado = outbox[0]
    assert enviado["email"] == "compradora@example.com"
    senha = enviado["temp_password"]
    assert senha, "sem a senha no e-mail o cliente não tem como entrar"

    # A prova que importa não é o e-mail ter saído, é a senha funcionar.
    login = client.post(
        "/api/auth/login",
        json={"email": "compradora@example.com", "password": senha},
    )
    assert login.status_code == 200, login.text


def test_cliente_que_ja_existia_nao_recebe_senha_nova(client, outbox, monkeypatch):
    """Reenviar senha para quem já tem conta trocaria a senha que ela escolheu."""
    secret = "s" * 32
    monkeypatch.setenv("CAKTO_WEBHOOK_SECRET", secret)

    _post(client, secret, _payload("evt-email-2", "recorrente@example.com"))
    _post(client, secret, _payload("evt-email-3", "recorrente@example.com"))

    assert len(outbox) == 2
    assert outbox[0]["temp_password"], "primeira compra cria a conta e manda a senha"
    assert outbox[1]["temp_password"] is None, "segunda compra não pode inventar senha nova"


def test_provedor_de_email_fora_nao_derruba_a_compra(client, monkeypatch):
    secret = "s" * 32
    monkeypatch.setenv("CAKTO_WEBHOOK_SECRET", secret)
    monkeypatch.setattr(
        main,
        "send_purchase_confirmation",
        lambda **kwargs: {"sent": False, "error": "RESEND_API_KEY ausente"},
    )

    response = _post(client, secret, _payload("evt-email-4", "semmail@example.com"))

    # O dinheiro entrou. Recusar a compra porque o e-mail falhou perderia a
    # venda por um problema que não é do cliente.
    assert response.status_code == 200
    assert response.json()["ok"] is True
