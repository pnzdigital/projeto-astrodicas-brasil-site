"""Diário Astral — trial sem cartão e fluxo de assinatura paga (AR).

O que estes testes protegem:

1. Trial sem cartão: /api/trial/start cria assinatura local (provider=none),
   concede acesso ao horóscopo e envia e-mail — tudo sem chamar o Mercado Pago.
2. Gate de acesso: durante o trial só horóscopo; mapa + guia + previsão só após
   primeira cobrança confirmada.
3. Mapa Astral vitalício: entitlement criado uma vez na primeira cobrança, nunca
   re-concedido na renovação do mês 2.
4. Renovação AR: webhook subscription_authorized_payment empurra o acesso 30d.
5. Cancelamento: provider=none não chama mp.cancel_preapproval; acesso preservado
   até o fim do período.
6. 1 trial por e-mail: segundo trial retorna 409.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import mercadopago as mp
from app import subscriptions
from app.db import SessionLocal
from app.models import Entitlement, Subscription, User


AGORA = datetime.now(timezone.utc)

TRIAL_BR = {"email": "cliente@astrodicas.com", "name": "Cliente BR", "locale": "pt-BR"}
TRIAL_AR = {"email": "cliente@astrodicas.com", "name": "Cliente AR", "locale": "es-AR"}


@pytest.fixture(autouse=True)
def env_configurado(monkeypatch):
    monkeypatch.setenv("MP_ACCESS_TOKEN", "TEST-token")
    monkeypatch.setenv("SITE_PUBLIC_URL", "https://astrodicas.example")


@pytest.fixture
def emails_enviados(monkeypatch):
    outbox = []
    monkeypatch.setattr(subscriptions, "send_trial_started", lambda **kwargs: outbox.append(("trial_started", kwargs)) or {"sent": True})
    monkeypatch.setattr(subscriptions, "send_purchase_confirmation", lambda **kwargs: outbox.append(("purchase", kwargs)) or {"sent": True})
    return outbox


def _assinatura(db):
    return db.scalar(select(Subscription))


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Trial sem cartão — fluxo completo
# ---------------------------------------------------------------------------

def test_trial_start_retorna_trialing_com_prazo(client):
    response = client.post("/api/trial/start", json=TRIAL_BR)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "trialing"
    assert "trial_ends_at" in body

    db = SessionLocal()
    try:
        sub = _assinatura(db)
        assert sub.status == "trialing"
        assert sub.provider == "none"
        assert sub.product_id == "site:diario_astral"
    finally:
        db.close()


def test_trial_concede_apenas_horoscopo(client):
    """Trial cobre só horóscopo diário. Mapa Astral NÃO deve existir durante o trial."""
    client.post("/api/trial/start", json=TRIAL_BR)

    db = SessionLocal()
    try:
        ent_plano = db.scalar(
            select(Entitlement).where(Entitlement.product_id == "site:diario_astral")
        )
        ent_mapa = db.scalar(
            select(Entitlement).where(Entitlement.product_id == "site:mapa_astral")
        )
        assert ent_plano is not None, "trial deve conceder site:diario_astral"
        assert ent_plano.expires_at is not None, "trial tem prazo"
        assert ent_mapa is None, "mapa_astral NÃO deve existir durante o trial"
    finally:
        db.close()


def test_trial_envia_email_de_boas_vindas(client, emails_enviados):
    client.post("/api/trial/start", json=TRIAL_BR)

    types = [e[0] for e in emails_enviados]
    assert "trial_started" in types


def test_trial_funciona_no_brasil_e_na_argentina(client):
    """Brasil agora pode fazer trial (sem cartão, sem dependência MLA)."""
    br = client.post("/api/trial/start", json=TRIAL_BR)
    assert br.status_code == 200, br.text

    # AR com e-mail diferente
    ar = client.post("/api/trial/start", json={**TRIAL_AR, "email": "ar@astrodicas.com"})
    assert ar.status_code == 200, ar.text


def test_um_trial_por_email(client):
    """Segundo trial com o mesmo e-mail retorna 409."""
    client.post("/api/trial/start", json=TRIAL_BR)
    segunda = client.post("/api/trial/start", json=TRIAL_BR)

    assert segunda.status_code == 409


def test_trial_nao_exige_cartao(client, monkeypatch):
    """card_token_id ignorado — trial não toca o Mercado Pago."""
    chamou_mp = []
    monkeypatch.setattr(mp, "create_preapproval", lambda **kw: chamou_mp.append(kw))

    response = client.post("/api/trial/start", json={**TRIAL_AR, "card_token_id": "nao-importa"})

    assert response.status_code == 200, response.text
    assert chamou_mp == [], "provider=none não deve chamar mp.create_preapproval"


# ---------------------------------------------------------------------------
# Gate de acesso — mapa_astral só após primeira cobrança
# ---------------------------------------------------------------------------

def _cria_assinatura_ar_pending(preapproval_id: str = "preapproval-1") -> str:
    """Insere diretamente uma Subscription pendente AR (provider=mercadopago).

    Necessário porque o webhook subscription_preapproval só atualiza assinaturas
    que já existem — a criação inicial vem do /api/trial/start antigo ou, no
    novo fluxo, do checkout externo. Para testes, criamos direto no banco.
    """
    from uuid import uuid4
    from app.pricing import amount_minor
    db = SessionLocal()
    try:
        user = User(
            email=TRIAL_AR["email"],
            password_hash="placeholder",
            name=TRIAL_AR["name"],
            locale="es-AR",
        )
        db.add(user)
        db.flush()
        sub = Subscription(
            user_id=user.id,
            provider="mercadopago",
            external_id=preapproval_id,
            product_id="site:diario_astral",
            status="pending",
            amount_minor=amount_minor("site:diario_astral", "es-AR"),
            currency="ARS",
            locale="es-AR",
            market="AR",
        )
        db.add(sub)
        db.commit()
        return sub.id
    finally:
        db.close()


def _autoriza_preapproval(client, monkeypatch, preapproval_id: str = "preapproval-1") -> None:
    """Simula webhook de autorização do Mercado Pago."""
    monkeypatch.setattr(
        mp,
        "get_preapproval",
        lambda pid: {"id": pid, "status": "authorized", "external_reference": preapproval_id},
    )
    client.post(
        "/api/webhooks/mercadopago/ar/subscription",
        json={"type": "subscription_preapproval", "data": {"id": preapproval_id}},
    )


def _assinatura_ar_paga(client, monkeypatch, preapproval_id: str = "preapproval-1") -> str:
    """Cria assinatura AR pendente, autoriza via webhook e processa primeiro pagamento."""
    sub_id = _cria_assinatura_ar_pending(preapproval_id)
    _autoriza_preapproval(client, monkeypatch, preapproval_id)
    # Simula primeiro pagamento aprovado
    monkeypatch.setattr(
        mp,
        "get_payment",
        lambda pid: {
            "id": pid,
            "status": "approved",
            "metadata": {"preapproval_id": preapproval_id},
        },
    )
    client.post(
        "/api/webhooks/mercadopago/ar/subscription",
        json={"type": "subscription_authorized_payment", "data": {"id": "payment-1"}},
    )
    return sub_id


def test_mapa_astral_concedido_apos_primeira_cobranca(client, monkeypatch):
    """mapa_astral só nasce no entitlement quando a primeira cobrança confirmar."""
    _assinatura_ar_paga(client, monkeypatch)


    db = SessionLocal()
    try:
        ent_mapa = db.scalar(
            select(Entitlement).where(Entitlement.product_id == "site:mapa_astral")
        )
        assert ent_mapa is not None, "mapa_astral deve existir após pagamento confirmado"
        assert ent_mapa.expires_at is None, "mapa_astral é vitalício (expires_at=None)"
        assert ent_mapa.status == "available"
    finally:
        db.close()


def test_renovacao_mes2_nao_toca_mapa_astral(client, monkeypatch):
    """Renovação do mês 2 NÃO deve criar novo entitlement de mapa_astral.
    O mapa é concedido uma única vez na primeira cobrança e é vitalício."""
    sub_id = _assinatura_ar_paga(client, monkeypatch)

    db = SessionLocal()
    try:
        mapa_antes = db.scalar(
            select(Entitlement).where(Entitlement.product_id == "site:mapa_astral")
        )
        mapa_id_antes = mapa_antes.id if mapa_antes else None
    finally:
        db.close()

    # Simula segundo pagamento (mês 2)
    monkeypatch.setattr(
        mp,
        "get_payment",
        lambda pid: {
            "id": pid,
            "status": "approved",
            "metadata": {"preapproval_id": "preapproval-1"},
        },
    )
    client.post(
        "/api/webhooks/mercadopago/ar/subscription",
        json={"type": "subscription_authorized_payment", "data": {"id": "payment-2"}},
    )

    db = SessionLocal()
    try:
        mapa_depois = db.scalar(
            select(Entitlement).where(Entitlement.product_id == "site:mapa_astral")
        )
        assert mapa_depois is not None
        assert mapa_depois.id == mapa_id_antes, "mesmo entitlement, não foi recriado"
        assert mapa_depois.expires_at is None, "ainda vitalício"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Renovação AR paga — webhook subscription_authorized_payment
# ---------------------------------------------------------------------------

def test_renovacao_paga_empurra_acesso_um_mes(client, monkeypatch):
    """Após a primeira cobrança, current_period_end avança ~30d e status=active."""
    sub_id = _cria_assinatura_ar_pending()
    _autoriza_preapproval(client, monkeypatch)

    db = SessionLocal()
    try:
        sub = db.get(Subscription, sub_id)
        antes = _aware(sub.current_period_end)
    finally:
        db.close()

    monkeypatch.setattr(
        mp,
        "get_payment",
        lambda pid: {"id": pid, "status": "approved", "metadata": {"preapproval_id": "preapproval-1"}},
    )
    resp = client.post(
        "/api/webhooks/mercadopago/ar/subscription",
        json={"type": "subscription_authorized_payment", "data": {"id": "payment-1"}},
    )

    assert resp.status_code == 200, resp.text
    db = SessionLocal()
    try:
        sub = db.get(Subscription, sub_id)
        depois = _aware(sub.current_period_end)
        assert depois > antes
        assert sub.status == "active"
    finally:
        db.close()


def test_mesma_renovacao_nao_conta_duas_vezes(client, monkeypatch):
    """MP reenvia até receber 200: payment-1 chegando duas vezes não empurra duas vezes."""
    _cria_assinatura_ar_pending()
    _autoriza_preapproval(client, monkeypatch)
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


# ---------------------------------------------------------------------------
# Cancelamento
# ---------------------------------------------------------------------------

def _loga(client, email: str, password: str) -> None:
    client.post("/api/auth/login", json={"email": email, "password": password})


def test_cancelar_trial_nao_chama_provedor(client, monkeypatch):
    """provider=none: cancelar não pode chamar mp.cancel_preapproval."""
    cancelados = []
    monkeypatch.setattr(mp, "cancel_preapproval", lambda pid: cancelados.append(pid))

    client.post("/api/trial/start", json=TRIAL_BR)

    from app.security import hash_password
    db = SessionLocal()
    try:
        user = db.scalar(select(User))
        user.password_hash = hash_password("senha-123")
        db.commit()
    finally:
        db.close()

    _loga(client, TRIAL_BR["email"], "senha-123")
    resp = client.post("/api/me/subscription/cancel")

    assert resp.status_code == 200, resp.text
    assert cancelados == [], "provider=none não deve chamar mp.cancel_preapproval"
    assert resp.json()["status"] == "cancelled"


def test_cancelar_mantem_acesso_ate_fim_do_prazo(client, monkeypatch):
    """Cancelamento não antecipa o vencimento do entitlement."""
    client.post("/api/trial/start", json=TRIAL_BR)

    from app.security import hash_password
    db = SessionLocal()
    try:
        user = db.scalar(select(User))
        user.password_hash = hash_password("senha-123")
        prazo_antes = _aware(_assinatura(db).current_period_end)
        db.commit()
    finally:
        db.close()

    _loga(client, TRIAL_BR["email"], "senha-123")
    client.post("/api/me/subscription/cancel")

    db = SessionLocal()
    try:
        ent = db.scalar(select(Entitlement).where(Entitlement.product_id == "site:diario_astral"))
        assert _aware(ent.expires_at) == prazo_antes
    finally:
        db.close()


def test_cancelamento_via_webhook_mp(client, monkeypatch):
    """Webhook do painel do MP com status=cancelled marca a assinatura como cancelada.

    Testa direto de pending → cancelled (sem passar por authorized) para evitar
    que a deduplicação por event_id bloqueie o segundo webhook com mesmo preapproval_id.
    O cancelamento em produção chega como evento separado; aqui simulamos direto.
    """
    _cria_assinatura_ar_pending(preapproval_id="preapproval-cancel")

    monkeypatch.setattr(
        mp,
        "get_preapproval",
        lambda pid: {"id": pid, "status": "cancelled", "external_reference": "preapproval-cancel"},
    )
    resp = client.post(
        "/api/webhooks/mercadopago/ar/subscription",
        json={"type": "subscription_preapproval", "data": {"id": "preapproval-cancel"}},
    )

    assert resp.status_code == 200, resp.text
    db = SessionLocal()
    try:
        sub = _assinatura(db)
        assert sub.status == "cancelled"
        assert sub.cancelled_at is not None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

def test_webhook_invalido_retorna_401(client, monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("MP_WEBHOOK_SECRET_AR", "clave-secreta-de-teste")

    resp = client.post(
        "/api/webhooks/mercadopago/ar/subscription",
        headers={"x-signature": "ts=1,v1=falsificado", "x-request-id": "req-1"},
        json={"type": "subscription_preapproval", "data": {"id": "preapproval-1"}},
    )

    assert resp.status_code == 401


def test_ruido_do_painel_responde_ok(client):
    resp = client.post(
        "/api/webhooks/mercadopago/ar/subscription",
        json={"type": "shipments", "data": {"id": "qualquer"}},
    )

    assert resp.status_code == 200
    assert "ignored" in resp.json()
