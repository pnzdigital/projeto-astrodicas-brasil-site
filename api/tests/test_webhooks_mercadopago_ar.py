"""A rota argentina do Mercado Pago tem clave secreta própria.

Cada aplicação do Mercado Pago assina com a sua clave. Uma rota por aplicação
permite trocar ou revogar o segredo de um mercado sem derrubar os outros — e
sem voltar a compartilhar credencial com outro projeto, que foi como o
MP_WEBHOOK_SECRET deste site nasceu.
"""

import hashlib
import hmac

import pytest

from app import mercadopago as mp

AR_SECRET = "clave-secreta-da-aplicacao-argentina"
LEGADA_SECRET = "clave-secreta-antiga"


def _assinar(secret: str, data_id: str, request_id: str, ts: str = "1700000000") -> str:
    manifest = f"id:{data_id.lower()};request-id:{request_id};ts:{ts};"
    v1 = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return f"ts={ts},v1={v1}"


@pytest.fixture(autouse=True)
def _ambiente_limpo(monkeypatch):
    """Sem clave nenhuma no ambiente; ENV fica com quem precisa dele."""
    monkeypatch.delenv("MP_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("MP_WEBHOOK_SECRET_AR", raising=False)
    # Sem token de acesso o checkout devolve 503 e nem chega a abrir o pedido.
    monkeypatch.setenv("MP_ACCESS_TOKEN", "TEST-token")
    monkeypatch.setenv("MP_PUBLIC_KEY", "APP_USR-0000aaaa-1111-2222-3333-444455556666")
    monkeypatch.setenv("SITE_PUBLIC_URL", "https://astrodicas.example")


def test_rota_ar_valida_com_a_clave_da_aplicacao_argentina(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("MP_WEBHOOK_SECRET_AR", AR_SECRET)
    assinatura = _assinar(AR_SECRET, "123", "req-1")
    assert mp.verify_signature(assinatura, "req-1", "123", "MP_WEBHOOK_SECRET_AR")


def test_rota_ar_recusa_assinatura_da_clave_errada(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("MP_WEBHOOK_SECRET_AR", AR_SECRET)
    monkeypatch.setenv("MP_WEBHOOK_SECRET", LEGADA_SECRET)
    # Evento assinado pela aplicação antiga não pode passar na rota argentina.
    assinatura = _assinar(LEGADA_SECRET, "123", "req-1")
    assert not mp.verify_signature(assinatura, "req-1", "123", "MP_WEBHOOK_SECRET_AR")


def test_ar_cai_na_clave_antiga_enquanto_a_nova_nao_existe(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    # Janela de deploy: o código sobe antes de a variável nova ser configurada.
    # Sem esse fallback, toda notificação argentina viraria 503 nesse intervalo.
    monkeypatch.setenv("MP_WEBHOOK_SECRET", LEGADA_SECRET)
    assert mp.webhook_secret("MP_WEBHOOK_SECRET_AR") == LEGADA_SECRET
    assinatura = _assinar(LEGADA_SECRET, "123", "req-1")
    assert mp.verify_signature(assinatura, "req-1", "123", "MP_WEBHOOK_SECRET_AR")


def test_a_clave_nova_tem_precedencia_sobre_a_antiga(monkeypatch):
    monkeypatch.setenv("MP_WEBHOOK_SECRET", LEGADA_SECRET)
    monkeypatch.setenv("MP_WEBHOOK_SECRET_AR", AR_SECRET)
    assert mp.webhook_secret("MP_WEBHOOK_SECRET_AR") == AR_SECRET


def test_rota_legada_nunca_herda_a_clave_argentina(monkeypatch):
    # O fallback é de mão única: a rota legada não pode ser validada por um
    # segredo que pertence a outra aplicação.
    monkeypatch.setenv("MP_WEBHOOK_SECRET_AR", AR_SECRET)
    assert mp.webhook_secret("MP_WEBHOOK_SECRET") == ""


def test_sem_nenhuma_clave_a_verificacao_recusa_em_producao(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("ALLOW_INSECURE_DEV", "1")  # ignorado em produção
    assert not mp.verify_signature("ts=1,v1=abc", "req-1", "123", "MP_WEBHOOK_SECRET_AR")


def test_as_duas_rotas_existem_no_app():
    from app.main import app

    caminhos = {r.path for r in app.routes}
    assert "/api/webhooks/mercadopago/ar/notify" in caminhos
    assert "/api/webhooks/mercadopago/notify" in caminhos, "rota legada mantém vivos os pagamentos em voo"


def test_pagamento_novo_aponta_para_a_rota_argentina(client, monkeypatch):
    """Passa pelo endpoint real: é o checkout que escolhe a URL, não o teste."""
    from app import mercadopago

    order = client.post(
        "/api/checkout/order",
        json={
            "product_id": "site:oferta_plano_lua_premium",
            "email": "ar@cliente.com",
            "name": "Cliente AR",
            "locale": "es-AR",
        },
    ).json()

    capturado = {}

    def _fake_create_payment(**kwargs):
        capturado.update(kwargs)
        return {
            "id": 999,
            "status": "approved",
            "status_detail": "accredited",
            "external_reference": kwargs["order_id"],
            "transaction_amount": kwargs["amount"],
        }

    monkeypatch.setattr(mercadopago, "create_payment", _fake_create_payment)
    response = client.post(
        "/api/checkout/payment",
        json={
            "order_id": order["order_id"],
            "form_data": {
                "payment_method_id": "visa",
                "token": "tok",
                "installments": 1,
                "payer": {"email": "ar@cliente.com"},
            },
        },
    )

    assert response.status_code == 200, response.text
    assert capturado["notification_url"].endswith("/api/webhooks/mercadopago/ar/notify")
