"""O Plano Lua por assinatura, começando com 7 dias grátis.

O que estes testes protegem, em ordem de dano se quebrar:

1. acesso que não acaba — cancelou, parou de pagar, e continua recebendo;
2. renovação contada duas vezes — o Mercado Pago reenvia notificação até
   receber 200, e um mês virar dois é dinheiro que ninguém pagou;
3. trial oferecido onde não existe recorrência (Brasil) — pedir cartão para um
   fluxo que o backend não tem.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import mercadopago as mp
from app import subscriptions
from app.db import SessionLocal
from app.models import Entitlement, Subscription, User


AGORA = datetime.now(timezone.utc)

TRIAL = {
    "email": "cliente@astrodicas.com",
    "name": "Cliente AR",
    "locale": "es-AR",
    "card_token_id": "card-token-de-teste",
}


@pytest.fixture(autouse=True)
def provedor_ligado(monkeypatch):
    monkeypatch.setenv("MP_ACCESS_TOKEN", "TEST-token")
    monkeypatch.setenv("SITE_PUBLIC_URL", "https://astrodicas.example")


@pytest.fixture
def preapproval_criado(monkeypatch):
    capturado = {}

    def _fake(**kwargs):
        capturado.update(kwargs)
        return {"id": "preapproval-1", "status": "authorized", "external_reference": kwargs["external_reference"]}

    monkeypatch.setattr(mp, "create_preapproval", _fake)
    return capturado


def _assinatura(db):
    return db.scalar(select(Subscription))


def test_trial_cria_conta_assinatura_e_acesso_com_prazo(client, preapproval_criado):
    response = client.post("/api/trial/start", json=TRIAL)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "trialing"
    assert body["trial_days"] == 7

    db = SessionLocal()
    try:
        assinatura = _assinatura(db)
        assert assinatura.external_id == "preapproval-1"
        assert assinatura.status == "trialing"
        entitlement = db.scalar(
            select(Entitlement).where(Entitlement.product_id == "site:plano_lua")
        )
        # O acesso nasce com prazo: é o que faz o trial acabar de verdade.
        assert entitlement.expires_at is not None
        prazo = entitlement.expires_at
        if prazo.tzinfo is None:
            prazo = prazo.replace(tzinfo=timezone.utc)
        assert timedelta(days=6) < prazo - AGORA < timedelta(days=8)
    finally:
        db.close()


def test_o_periodo_gratis_e_pedido_ao_provedor(client, preapproval_criado):
    """Contar os 7 dias do nosso lado exigiria disparar a 1ª cobrança sozinhos."""
    client.post("/api/trial/start", json=TRIAL)

    assert preapproval_criado["trial_days"] == 7
    assert preapproval_criado["currency"] == "ARS"
    assert preapproval_criado["notification_url"].endswith("/api/webhooks/mercadopago/ar/subscription")


def test_o_valor_vem_do_servidor_e_nao_do_navegador(client, preapproval_criado):
    from app import pricing

    client.post("/api/trial/start", json={**TRIAL, "card_token_id": "outro-token"})

    esperado = pricing.amount_minor("site:plano_lua", "es-AR") / 100
    assert preapproval_criado["amount"] == esperado


def test_brasil_nao_recebe_trial_com_cartao(client, preapproval_criado):
    """Não existe conta recorrente brasileira: pedir cartão prometeria o que não há."""
    response = client.post("/api/trial/start", json={**TRIAL, "locale": "pt-BR"})

    assert response.status_code == 409
    assert isinstance(response.json()["detail"], str)

    db = SessionLocal()
    try:
        assert _assinatura(db) is None
    finally:
        db.close()


def test_cartao_recusado_nao_deixa_assinatura_orfa(client, monkeypatch):
    def _recusa(**kwargs):
        raise mp.MercadoPagoError("cartão recusado")

    monkeypatch.setattr(mp, "create_preapproval", _recusa)
    response = client.post("/api/trial/start", json=TRIAL)

    assert response.status_code == 402
    db = SessionLocal()
    try:
        assert _assinatura(db) is None, "linha órfã que nenhum webhook encontraria"
    finally:
        db.close()


def test_nao_da_dois_trials_para_a_mesma_conta(client, preapproval_criado):
    assert client.post("/api/trial/start", json=TRIAL).status_code == 200
    segunda = client.post("/api/trial/start", json=TRIAL)

    assert segunda.status_code == 409


def test_cancelar_avisa_o_provedor_e_mantem_o_acesso_ate_o_fim(client, preapproval_criado, monkeypatch):
    """Cortar na hora seria cobrar por um período e não entregar."""
    cancelados = []
    monkeypatch.setattr(mp, "cancel_preapproval", lambda pid: cancelados.append(pid) or {"status": "cancelled"})

    client.post("/api/trial/start", json=TRIAL)
    db = SessionLocal()
    try:
        user = db.scalar(select(User))
        from app.security import hash_password

        user.password_hash = hash_password("senha-de-teste-123")
        db.commit()
    finally:
        db.close()
    client.post("/api/auth/login", json={"email": TRIAL["email"], "password": "senha-de-teste-123"})

    response = client.post("/api/me/subscription/cancel")

    assert response.status_code == 200, response.text
    assert cancelados == ["preapproval-1"]
    assert response.json()["status"] == "cancelled"
    assert response.json()["access_until"], "o acesso vale até o fim do que já foi concedido"

    db = SessionLocal()
    try:
        entitlement = db.scalar(select(Entitlement).where(Entitlement.product_id == "site:plano_lua"))
        assert entitlement.expires_at is not None
    finally:
        db.close()


def test_provedor_fora_do_ar_nao_impede_o_cancelamento(client, preapproval_criado, monkeypatch):
    """Cancelar é a promessa da landing. Divergência temporária é o mal menor."""
    def _falha(pid):
        raise mp.MercadoPagoError("provedor indisponível")

    monkeypatch.setattr(mp, "cancel_preapproval", _falha)
    client.post("/api/trial/start", json=TRIAL)
    db = SessionLocal()
    try:
        user = db.scalar(select(User))
        from app.security import hash_password

        user.password_hash = hash_password("senha-de-teste-123")
        db.commit()
    finally:
        db.close()
    client.post("/api/auth/login", json={"email": TRIAL["email"], "password": "senha-de-teste-123"})

    assert client.post("/api/me/subscription/cancel").status_code == 200


def test_renovacao_paga_empurra_o_acesso_um_mes(client, preapproval_criado, monkeypatch):
    client.post("/api/trial/start", json=TRIAL)
    db = SessionLocal()
    try:
        assinatura = _assinatura(db)
        antes = assinatura.current_period_end
        if antes.tzinfo is None:
            antes = antes.replace(tzinfo=timezone.utc)
        assinatura_id = assinatura.id
    finally:
        db.close()

    monkeypatch.setattr(
        mp,
        "get_payment",
        lambda pid: {"id": pid, "status": "approved", "metadata": {"preapproval_id": "preapproval-1"}},
    )
    response = client.post(
        "/api/webhooks/mercadopago/ar/subscription",
        json={"type": "subscription_authorized_payment", "data": {"id": "payment-1"}},
    )

    assert response.status_code == 200, response.text
    db = SessionLocal()
    try:
        assinatura = db.get(Subscription, assinatura_id)
        depois = assinatura.current_period_end
        if depois.tzinfo is None:
            depois = depois.replace(tzinfo=timezone.utc)
        assert depois > antes
        assert assinatura.status == "active"
        entitlement = db.scalar(select(Entitlement).where(Entitlement.product_id == "site:plano_lua"))
        prazo = entitlement.expires_at
        if prazo.tzinfo is None:
            prazo = prazo.replace(tzinfo=timezone.utc)
        assert prazo == depois, "o entitlement é quem o portal consulta"
    finally:
        db.close()


def test_a_mesma_renovacao_reenviada_nao_conta_duas_vezes(client, preapproval_criado, monkeypatch):
    """O Mercado Pago reenvia até receber 200: um mês não pode virar dois."""
    client.post("/api/trial/start", json=TRIAL)
    monkeypatch.setattr(
        mp,
        "get_payment",
        lambda pid: {"id": pid, "status": "approved", "metadata": {"preapproval_id": "preapproval-1"}},
    )
    evento = {"type": "subscription_authorized_payment", "data": {"id": "payment-1"}}

    client.post("/api/webhooks/mercadopago/ar/subscription", json=evento)
    db = SessionLocal()
    try:
        primeiro = _assinatura(db).current_period_end
    finally:
        db.close()

    segunda = client.post("/api/webhooks/mercadopago/ar/subscription", json=evento)

    assert segunda.json().get("duplicate") is True
    db = SessionLocal()
    try:
        assert _assinatura(db).current_period_end == primeiro
    finally:
        db.close()


def test_cancelamento_no_painel_do_provedor_chega_pelo_webhook(client, preapproval_criado, monkeypatch):
    client.post("/api/trial/start", json=TRIAL)
    monkeypatch.setattr(mp, "get_preapproval", lambda pid: {"id": pid, "status": "cancelled"})

    response = client.post(
        "/api/webhooks/mercadopago/ar/subscription",
        json={"type": "subscription_preapproval", "data": {"id": "preapproval-1"}},
    )

    assert response.status_code == 200, response.text
    db = SessionLocal()
    try:
        assinatura = _assinatura(db)
        assert assinatura.status == "cancelled"
        assert assinatura.cancelled_at is not None
    finally:
        db.close()


def test_assinatura_invalida_e_recusada_no_webhook(client, monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("MP_WEBHOOK_SECRET_AR", "clave-secreta-de-teste")

    response = client.post(
        "/api/webhooks/mercadopago/ar/subscription",
        headers={"x-signature": "ts=1,v1=falsificado", "x-request-id": "req-1"},
        json={"type": "subscription_preapproval", "data": {"id": "preapproval-1"}},
    )

    assert response.status_code == 401


def test_ruido_do_painel_responde_ok_sem_mexer_em_nada(client, preapproval_criado):
    client.post("/api/trial/start", json=TRIAL)

    response = client.post(
        "/api/webhooks/mercadopago/ar/subscription",
        json={"type": "shipments", "data": {"id": "qualquer"}},
    )

    assert response.status_code == 200
    assert "ignored" in response.json()
