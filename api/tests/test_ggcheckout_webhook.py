"""Testes do adapter de webhook GG Checkout (mercado BR).

Cobre:
- Payload real (paid) libera acesso e envia email
- Secret errado rejeita (401)
- Secret via Authorization: Bearer também aceita
- Evento repetido não duplica (GG retenta até 3x)
- status pending/failed não libera nada
- status refunded/charged_back registra evento e não revoga (sem mecanismo)
- product.id não mapeado → 422 + log
- GG_CHECKOUT_SECRET ausente → 503 em prod, 200 em dev com ALLOW_INSECURE_DEV=1
- GG_PRODUCT_MAP JSON inválido → 422
- Argentina/Mercado Pago sem regressão
- Checkout order BR sem URL → 503 com mensagem clara
"""

import json
import logging

import pytest
from sqlalchemy import select

from app import checkout as site_checkout
from app import ggcheckout
from app.db import SessionLocal
from app.models import Entitlement, Order, User, WebhookEvent


GG_SECRET = "gg-secret-de-teste-32-bytes-min!"
GG_PRODUCT_ID = "gg-uuid-plano-lua"
GG_PRODUCT_MAP_JSON = json.dumps({GG_PRODUCT_ID: "site:plano_lua"})
GG_CHECKOUT_URLS_JSON = json.dumps({"site:plano_lua": "https://checkout.gg.test/plano-lua"})


def _gg_payload(
    payment_id: str = "pay-001",
    status: str = "paid",
    email: str = "cliente@br.com",
    gg_product_id: str = GG_PRODUCT_ID,
) -> dict:
    return {
        "webhook": {"id": f"wh-{payment_id}", "businessId": "biz-1"},
        "payment": {"id": payment_id, "status": status},
        "customer": {"email": email, "name": "Joana Silva"},
        "product": {"id": gg_product_id},
        "products": [{"id": gg_product_id}],
        "utm_source": None,
        "utm_medium": None,
    }


@pytest.fixture(autouse=True)
def _gg_env(monkeypatch):
    monkeypatch.setenv("GG_CHECKOUT_SECRET", GG_SECRET)
    monkeypatch.setenv("GG_PRODUCT_MAP", GG_PRODUCT_MAP_JSON)
    monkeypatch.setenv("GG_CHECKOUT_URLS", GG_CHECKOUT_URLS_JSON)


@pytest.fixture()
def sent_emails(monkeypatch):
    outbox = []
    monkeypatch.setattr(site_checkout, "send_purchase_confirmation", lambda **kw: outbox.append(kw) or {"sent": True})
    return outbox


# ── Autenticação ──────────────────────────────────────────────────────────────

def test_secret_errado_retorna_401(client):
    response = client.post(
        "/api/webhooks/ggcheckout",
        json=_gg_payload(),
        headers={"x-secret": "secreto-errado"},
    )
    assert response.status_code == 401


def test_authorization_bearer_aceito(client, sent_emails):
    response = client.post(
        "/api/webhooks/ggcheckout",
        json=_gg_payload("pay-bearer"),
        headers={"Authorization": f"Bearer {GG_SECRET}"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_x_secret_header_aceito(client, sent_emails):
    response = client.post(
        "/api/webhooks/ggcheckout",
        json=_gg_payload("pay-xsecret"),
        headers={"x-secret": GG_SECRET},
    )
    assert response.status_code == 200


def test_secret_ausente_producao_retorna_503(client, monkeypatch):
    monkeypatch.delenv("GG_CHECKOUT_SECRET", raising=False)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("ALLOW_INSECURE_DEV", raising=False)
    response = client.post("/api/webhooks/ggcheckout", json=_gg_payload("pay-prod"))
    assert response.status_code == 503
    assert "GG_CHECKOUT_SECRET" in response.json()["detail"]


def test_secret_ausente_dev_allow_insecure_aceito(client, monkeypatch, sent_emails):
    monkeypatch.delenv("GG_CHECKOUT_SECRET", raising=False)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("ALLOW_INSECURE_DEV", "1")
    response = client.post("/api/webhooks/ggcheckout", json=_gg_payload("pay-dev-allow"))
    assert response.status_code == 200


# ── Fulfillment (paid) ────────────────────────────────────────────────────────

def test_paid_libera_acesso_e_envia_email(client, sent_emails):
    response = client.post(
        "/api/webhooks/ggcheckout",
        json=_gg_payload("pay-full", email="nova@cliente.com.br"),
        headers={"x-secret": GG_SECRET},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["status"] == "paid"
    assert body["product_id"] == "site:plano_lua"

    # Verifica entitlement no banco
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == "nova@cliente.com.br"))
        assert user is not None
        entitlement = db.scalar(
            select(Entitlement).where(
                Entitlement.user_id == user.id,
                Entitlement.product_id == "site:plano_lua",
            )
        )
        assert entitlement is not None
        assert entitlement.status == "available"
    finally:
        db.close()

    assert len(sent_emails) == 1
    assert sent_emails[0]["email"] == "nova@cliente.com.br"
    assert sent_emails[0]["temp_password"]


def test_paid_cria_order_com_amount_do_pricing(client, sent_emails):
    from app import pricing
    response = client.post(
        "/api/webhooks/ggcheckout",
        json=_gg_payload("pay-amount", email="amount@cliente.com.br"),
        headers={"x-secret": GG_SECRET},
    )
    assert response.status_code == 200
    db = SessionLocal()
    try:
        order = db.scalar(select(Order).where(Order.external_id == "pay-amount"))
        assert order is not None
        assert order.amount_minor == pricing.amount_minor("site:plano_lua", "pt-BR")
        assert order.currency == "BRL"
        assert order.provider == "ggcheckout"
        assert order.locale == "pt-BR"
    finally:
        db.close()


# ── Idempotência ──────────────────────────────────────────────────────────────

def test_retry_3x_nao_duplica_acesso_nem_email(client, sent_emails):
    """GG retenta até 3×: só o primeiro processamento libera, os outros retornam duplicate."""
    payload = _gg_payload("pay-retry", email="retry@cliente.com.br")
    for i in range(3):
        r = client.post("/api/webhooks/ggcheckout", json=payload, headers={"x-secret": GG_SECRET})
        assert r.status_code == 200

    # Evento no banco: apenas 1
    db = SessionLocal()
    try:
        events = db.scalars(
            select(WebhookEvent).where(WebhookEvent.event_id == "gg:pay-retry")
        ).all()
        assert len(events) == 1
    finally:
        db.close()

    # Email enviado apenas 1×
    assert len(sent_emails) == 1

    # Segunda e terceira chamada retornaram duplicate
    r2 = client.post("/api/webhooks/ggcheckout", json=payload, headers={"x-secret": GG_SECRET})
    assert r2.json().get("duplicate") is True


# ── Status que não liberam acesso ────────────────────────────────────────────

@pytest.mark.parametrize("status", ["pending", "failed"])
def test_status_nao_paid_nao_libera_acesso(client, sent_emails, status):
    response = client.post(
        "/api/webhooks/ggcheckout",
        json=_gg_payload(f"pay-{status}", status=status, email=f"{status}@cliente.com.br"),
        headers={"x-secret": GG_SECRET},
    )
    assert response.status_code == 200
    assert response.json()["action"] == "ignored"
    assert sent_emails == []

    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == f"{status}@cliente.com.br"))
        assert user is None
    finally:
        db.close()


# ── Revogação (refunded / charged_back) ──────────────────────────────────────

@pytest.mark.parametrize("status", ["refunded", "charged_back"])
def test_revoke_status_registra_evento_e_nao_revoga(client, sent_emails, status, caplog):
    with caplog.at_level(logging.WARNING, logger="app.ggcheckout"):
        response = client.post(
            "/api/webhooks/ggcheckout",
            json=_gg_payload(f"pay-{status}", status=status, email=f"{status}@cliente.com.br"),
            headers={"x-secret": GG_SECRET},
        )
    assert response.status_code == 200
    assert response.json()["action"] == "logged"
    # Evento registrado
    db = SessionLocal()
    try:
        event = db.scalar(select(WebhookEvent).where(WebhookEvent.event_id == f"gg:pay-{status}"))
        assert event is not None
    finally:
        db.close()
    # Log de aviso emitido
    assert any("mecanismo pendente" in r.message.lower() or status in r.message for r in caplog.records)
    assert sent_emails == []


# ── Produto não mapeado ──────────────────────────────────────────────────────

def test_produto_nao_mapeado_retorna_422_e_loga(client, sent_emails, caplog):
    payload = _gg_payload("pay-nomap", gg_product_id="gg-uuid-desconhecido")
    with caplog.at_level(logging.ERROR, logger="app.ggcheckout"):
        response = client.post(
            "/api/webhooks/ggcheckout",
            json=payload,
            headers={"x-secret": GG_SECRET},
        )
    assert response.status_code == 422
    assert "GG_PRODUCT_MAP" in response.json()["detail"]
    assert any("GG_PRODUCT_MAP" in r.message for r in caplog.records)
    assert sent_emails == []


def test_produto_nao_mapeado_nao_persiste_evento_e_permite_retentativa(
    client, monkeypatch, sent_emails
):
    """Falha de configuração (produto fora do mapa) NÃO deve persistir o evento.

    Fluxo real: GG dispara webhook → 422 porque GG_PRODUCT_MAP ainda não
    tem o UID. Operador adiciona o UID ao mapa. GG retenta. Segunda chamada
    com mesmo payment_id deve processar normalmente (sem cair no bloco
    de idempotência que devolve 'duplicate: true').
    """
    payment_id = "pay-retry-nomap"
    gg_uid = "gg-uuid-sem-mapa"
    payload = _gg_payload(payment_id, email="retry@br.com", gg_product_id=gg_uid)

    # Primeiro envio: mapa vazio → 422
    monkeypatch.setenv("GG_PRODUCT_MAP", json.dumps({}))
    resp1 = client.post(
        "/api/webhooks/ggcheckout",
        json=payload,
        headers={"x-secret": GG_SECRET},
    )
    assert resp1.status_code == 422
    assert gg_uid in resp1.json()["detail"]

    # Evento NÃO está no BD — retentativa possível
    db = SessionLocal()
    try:
        event = db.scalar(
            select(WebhookEvent).where(WebhookEvent.event_id == f"gg:{payment_id}")
        )
        assert event is None, "Evento de config-error não deve ser persistido"
    finally:
        db.close()

    # Operador corrige o mapa; GG retenta — deve liberar acesso
    monkeypatch.setenv("GG_PRODUCT_MAP", GG_PRODUCT_MAP_JSON.replace(GG_PRODUCT_ID, gg_uid))
    resp2 = client.post(
        "/api/webhooks/ggcheckout",
        json=payload,
        headers={"x-secret": GG_SECRET},
    )
    assert resp2.status_code == 200
    assert resp2.json().get("product_id") == "site:plano_lua"

    # Acesso liberado
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == "retry@br.com"))
        assert user is not None
        ent = db.scalar(
            select(Entitlement).where(
                Entitlement.user_id == user.id,
                Entitlement.product_id == "site:plano_lua",
            )
        )
        assert ent is not None and ent.status == "available"
    finally:
        db.close()


def test_gg_product_map_json_invalido_retorna_422(client, monkeypatch, sent_emails):
    monkeypatch.setenv("GG_PRODUCT_MAP", "nao-e-json")
    response = client.post(
        "/api/webhooks/ggcheckout",
        json=_gg_payload("pay-badjson"),
        headers={"x-secret": GG_SECRET},
    )
    assert response.status_code == 422


# ── Perna de saída: order BR sem URL ─────────────────────────────────────────

def test_order_br_sem_url_retorna_503_com_mensagem_clara(client, monkeypatch):
    monkeypatch.delenv("GG_CHECKOUT_URLS", raising=False)
    response = client.post(
        "/api/checkout/order",
        json={"product_id": "site:plano_lua", "email": "sem-url@cliente.com", "locale": "pt-BR"},
    )
    assert response.status_code == 503
    assert "checkout" in response.json()["detail"].lower()


def test_order_br_sem_url_loga_erro(client, monkeypatch, caplog):
    monkeypatch.delenv("GG_CHECKOUT_URLS", raising=False)
    with caplog.at_level(logging.ERROR, logger="app.checkout"):
        client.post(
            "/api/checkout/order",
            json={"product_id": "site:mapa_astral", "email": "log@cliente.com", "locale": "pt-BR"},
        )
    assert any("GG_CHECKOUT_URLS" in r.message for r in caplog.records)


def test_order_br_com_url_retorna_init_point_com_email_e_ref(client):
    response = client.post(
        "/api/checkout/order",
        json={"product_id": "site:plano_lua", "email": "oi@cliente.com.br", "name": "Oi", "locale": "pt-BR"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["init_point"].startswith("https://checkout.gg.test/plano-lua")
    assert "email=oi%40cliente.com.br" in body["init_point"] or "email=oi@cliente.com.br" in body["init_point"]
    assert "ref=" in body["init_point"]
    assert body["redirect"] is True


def test_order_br_url_json_invalido_retorna_503(client, monkeypatch):
    monkeypatch.setenv("GG_CHECKOUT_URLS", "nao-e-json")
    response = client.post(
        "/api/checkout/order",
        json={"product_id": "site:plano_lua", "email": "bad@cliente.com", "locale": "pt-BR"},
    )
    assert response.status_code == 503


# ── Regressão Argentina / Mercado Pago ───────────────────────────────────────

@pytest.fixture(autouse=True)
def _mp_env(monkeypatch):
    monkeypatch.setenv("MP_ACCESS_TOKEN", "TEST-token")
    monkeypatch.setenv("MP_PUBLIC_KEY", "APP_USR-0000aaaa-1111-2222-3333-444455556666")
    monkeypatch.setenv("SITE_PUBLIC_URL", "https://astrodicas.example")


def test_argentina_continua_usando_mercadopago(client):
    response = client.post(
        "/api/checkout/order",
        json={"product_id": "site:oferta_plano_lua_premium", "email": "garcia@cliente.ar", "locale": "es-AR"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "mercadopago.com" in body["init_point"]
    assert body["redirect"] is True
    assert body["currency"] == "ARS"


def test_catalogo_ar_provider_nao_mudou(client):
    catalog = client.get("/api/catalog?locale=es-AR").json()
    assert catalog["checkout"]["provider"] == "mercadopago"
    assert catalog["checkout"]["redirect"] is True
