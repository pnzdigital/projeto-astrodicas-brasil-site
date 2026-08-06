import hashlib
import hmac

import pytest
from sqlalchemy import select

from app import checkout, mercadopago, pricing
from conftest import register


@pytest.fixture(autouse=True)
def mercadopago_credentials(monkeypatch):
    monkeypatch.setenv("MP_ACCESS_TOKEN", "TEST-token")
    monkeypatch.setenv("MP_PUBLIC_KEY", "APP_USR-0000aaaa-1111-2222-3333-444455556666")
    monkeypatch.setenv("SITE_PUBLIC_URL", "https://astrodicas.example")


@pytest.fixture()
def sent_emails(monkeypatch):
    outbox = []
    monkeypatch.setattr(checkout, "send_purchase_confirmation", lambda **kwargs: outbox.append(kwargs) or {"sent": True})
    return outbox


def test_argentine_price_is_the_brazilian_price_converted():
    """A conversão vale para o catálogo geral. Plano Lua, Premium e a oferta de
    saída têm preço argentino próprio, definido comercialmente — esses estão em
    test_pricing_ar_overrides."""
    assert pricing.amount_minor("site:mapa_astral", "pt-BR") == 4700
    assert pricing.amount_minor("site:mapa_astral", "es-AR") == 4700 * 310
    ratio_mapa = pricing.amount_minor("site:mapa_astral", "es-AR") / pricing.amount_minor("site:mapa_astral", "pt-BR")
    ratio_combo = pricing.amount_minor("site:combo_mapa_astral_amor", "es-AR") / pricing.amount_minor("site:combo_mapa_astral_amor", "pt-BR")
    assert ratio_mapa == ratio_combo
    # Preços próprios do mercado AR.
    assert pricing.format_amount(pricing.amount_minor("site:plano_lua", "es-AR"), "ARS") == "ARS 9.900"
    assert pricing.format_amount(pricing.amount_minor("site:oferta_plano_lua_premium", "es-AR"), "ARS") == "ARS 34.900"


def test_catalog_endpoint_serves_each_market(client):
    br = client.get("/api/catalog?locale=pt-BR").json()
    ar = client.get("/api/catalog?locale=es-AR").json()
    assert br["currency"] == "BRL" and ar["currency"] == "ARS"
    assert ar["checkout"]["provider"] == "mercadopago" and ar["checkout"]["transparent"] is True
    lua_ar = next(p for p in ar["products"] if p["product_id"] == "site:plano_lua")
    assert lua_ar["price_label"] == "ARS 9.900"


def test_order_uses_server_price_and_rejects_unknown_product(client):
    unknown = client.post("/api/checkout/order", json={"product_id": "site:nope", "email": "a@b.com", "locale": "es-AR"})
    assert unknown.status_code == 404

    response = client.post(
        "/api/checkout/order",
        json={"product_id": "site:oferta_plano_lua_premium", "email": "Cliente@Example.com", "name": "Cliente Teste", "locale": "es-AR"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["amount"] == 34900.0
    assert body["amount_minor"] == pricing.PRICES_ARS_MINOR["site:oferta_plano_lua_premium"]
    assert body["currency"] == "ARS"


def test_payment_approved_creates_account_grants_bundle_and_emails(client, monkeypatch, sent_emails):
    order = client.post(
        "/api/checkout/order",
        json={"product_id": "site:oferta_plano_lua_premium", "email": "nova@cliente.com", "name": "Nova Cliente", "locale": "es-AR"},
    ).json()

    captured = {}

    def fake_create_payment(**kwargs):
        captured.update(kwargs)
        return {"id": 12345, "status": "approved", "status_detail": "accredited", "external_reference": kwargs["order_id"], "transaction_amount": kwargs["amount"]}

    monkeypatch.setattr(mercadopago, "create_payment", fake_create_payment)

    response = client.post(
        "/api/checkout/payment",
        json={"order_id": order["order_id"], "form_data": {"payment_method_id": "visa", "token": "tok", "installments": 1, "payer": {"email": "nova@cliente.com"}}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["approved"] is True
    # O valor cobrado é o do servidor, não o que o navegador mandou.
    assert captured["amount"] == 34900.0

    login = client.post("/api/auth/login", json={"email": "nova@cliente.com", "password": "qualquer"})
    assert login.status_code == 401  # senha provisória é aleatória, não adivinhável

    assert len(sent_emails) == 1
    assert sent_emails[0]["locale"] == "es-AR"
    assert sent_emails[0]["temp_password"]

    admin_login = client.post("/api/admin/login", json={"password": "painel-teste"})
    assert admin_login.status_code in {200, 401, 503}


def test_purchase_conversion_only_exposes_confirmed_order(client, monkeypatch, sent_emails):
    order = client.post(
        "/api/checkout/order",
        json={"product_id": "site:oferta_plano_lua_premium", "email": "pixel@cliente.com", "locale": "es-AR"},
    ).json()

    pending = client.get(f"/api/checkout/order/{order['order_id']}/conversion")
    assert pending.status_code == 404

    monkeypatch.setattr(
        mercadopago,
        "create_payment",
        lambda **kwargs: {"id": 2468, "status": "approved", "external_reference": kwargs["order_id"]},
    )
    paid = client.post(
        "/api/checkout/payment",
        json={
            "order_id": order["order_id"],
            "form_data": {"payment_method_id": "visa", "token": "tok", "payer": {"email": "pixel@cliente.com"}},
        },
    )
    assert paid.json()["approved"] is True

    conversion = client.get(f"/api/checkout/order/{order['order_id']}/conversion")
    assert conversion.status_code == 200
    assert conversion.json() == {
        "event_id": f"site-purchase-{order['order_id']}",
        "order_id": order["order_id"],
        "content_ids": ["site:oferta_plano_lua_premium"],
        "content_name": "Plan Luna Premium",
        "content_type": "product",
        "num_items": 1,
        "value": 34900.0,
        "currency": "ARS",
        "content_language": "es-AR",
    }
    assert "email" not in conversion.json()


def test_payment_is_not_charged_twice_and_email_is_sent_once(client, monkeypatch, sent_emails):
    order = client.post(
        "/api/checkout/order",
        json={"product_id": "site:mapa_astral", "email": "repete@cliente.com", "locale": "es-AR"},
    ).json()
    monkeypatch.setattr(
        mercadopago,
        "create_payment",
        lambda **kwargs: {"id": 999, "status": "approved", "external_reference": kwargs["order_id"]},
    )
    payload = {"order_id": order["order_id"], "form_data": {"payment_method_id": "visa", "token": "t", "payer": {"email": "repete@cliente.com"}}}
    first = client.post("/api/checkout/payment", json=payload)
    second = client.post("/api/checkout/payment", json=payload)
    assert first.json()["approved"] is True
    assert second.json()["status"] == "approved"
    assert len(sent_emails) == 1


def test_rejected_payment_does_not_grant_access(client, monkeypatch, sent_emails):
    order = client.post(
        "/api/checkout/order",
        json={"product_id": "site:mapa_carreira", "email": "recusa@cliente.com", "locale": "es-AR"},
    ).json()
    monkeypatch.setattr(
        mercadopago,
        "create_payment",
        lambda **kwargs: {"id": 1, "status": "rejected", "status_detail": "cc_rejected_other_reason", "external_reference": kwargs["order_id"]},
    )
    response = client.post(
        "/api/checkout/payment",
        json={"order_id": order["order_id"], "form_data": {"payment_method_id": "visa", "token": "t", "payer": {"email": "recusa@cliente.com"}}},
    )
    assert response.json()["approved"] is False
    assert sent_emails == []


def test_webhook_notification_verifies_signature_and_is_idempotent(client, monkeypatch, sent_emails):
    monkeypatch.setenv("MP_WEBHOOK_SECRET", "clave-secreta")
    order = client.post(
        "/api/checkout/order",
        json={"product_id": "site:plano_lua", "email": "hook@cliente.com", "locale": "es-AR"},
    ).json()
    monkeypatch.setattr(
        mercadopago,
        "get_payment",
        lambda payment_id: {"id": payment_id, "status": "approved", "external_reference": order["order_id"]},
    )

    body = {"type": "payment", "data": {"id": "778899"}}
    manifest = "id:778899;request-id:req-1;ts:1700000000;"
    signature = hmac.new(b"clave-secreta", manifest.encode(), hashlib.sha256).hexdigest()
    headers = {"x-signature": f"ts=1700000000,v1={signature}", "x-request-id": "req-1"}

    forged = client.post("/api/webhooks/mercadopago/notify", json=body, headers={"x-signature": "ts=1700000000,v1=deadbeef", "x-request-id": "req-1"})
    assert forged.status_code == 401

    first = client.post("/api/webhooks/mercadopago/notify", json=body, headers=headers)
    assert first.status_code == 200 and first.json()["status"] == "paid"
    duplicate = client.post("/api/webhooks/mercadopago/notify", json=body, headers=headers)
    assert duplicate.json()["duplicate"] is True
    assert len(sent_emails) == 1


def test_order_rejected_when_mp_not_enabled_for_ar(client, monkeypatch):
    monkeypatch.delenv("MP_ACCESS_TOKEN", raising=False)
    response = client.post(
        "/api/checkout/order",
        json={"product_id": "site:plano_lua", "email": "a@b.com", "locale": "es-AR"},
    )
    assert response.status_code == 503


def test_payment_for_unknown_order_returns_404(client):
    response = client.post(
        "/api/checkout/payment",
        json={"order_id": "does-not-exist", "form_data": {"payment_method_id": "visa"}},
    )
    assert response.status_code == 404


def test_payment_without_payment_method_returns_400(client):
    order = client.post(
        "/api/checkout/order",
        json={"product_id": "site:mapa_astral", "email": "sem-metodo@cliente.com", "locale": "es-AR"},
    ).json()
    response = client.post(
        "/api/checkout/payment",
        json={"order_id": order["order_id"], "form_data": {"token": "tok"}},
    )
    assert response.status_code == 400


def test_payment_without_payer_email_returns_400(client):
    from app.db import SessionLocal
    from app.models import Order

    order = client.post(
        "/api/checkout/order",
        json={"product_id": "site:mapa_astral", "email": "sera-limpo@cliente.com", "locale": "es-AR"},
    ).json()

    db = SessionLocal()
    try:
        db.get(Order, order["order_id"]).customer_email = None
        db.commit()
    finally:
        db.close()

    response = client.post(
        "/api/checkout/payment",
        json={"order_id": order["order_id"], "form_data": {"payment_method_id": "visa", "token": "t"}},
    )
    assert response.status_code == 400


def test_payment_provider_error_returns_502(client, monkeypatch):
    order = client.post(
        "/api/checkout/order",
        json={"product_id": "site:mapa_astral", "email": "falha@cliente.com", "locale": "es-AR"},
    ).json()

    def boom(**kwargs):
        raise mercadopago.MercadoPagoError("timeout")

    monkeypatch.setattr(mercadopago, "create_payment", boom)
    response = client.post(
        "/api/checkout/payment",
        json={"order_id": order["order_id"], "form_data": {"payment_method_id": "visa", "token": "t", "payer": {"email": "falha@cliente.com"}}},
    )
    assert response.status_code == 502


def test_notify_without_data_id_is_ignored(client):
    response = client.post("/api/webhooks/mercadopago/notify", json={"type": "payment"})
    assert response.status_code == 200
    assert response.json()["ignored"] == "sem data.id"


def test_notify_without_secret_returns_503_in_production(client, monkeypatch):
    monkeypatch.delenv("MP_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("ALLOW_INSECURE_DEV", raising=False)
    response = client.post(
        "/api/webhooks/mercadopago/notify",
        json={"type": "payment", "data": {"id": "1"}},
    )
    assert response.status_code == 503


def test_notify_ignores_unrelated_topic(client, monkeypatch):
    monkeypatch.delenv("MP_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ALLOW_INSECURE_DEV", "1")
    response = client.post(
        "/api/webhooks/mercadopago/notify",
        json={"type": "merchant_order", "data": {"id": "1"}},
    )
    assert response.status_code == 200
    assert response.json()["ignored"] == "merchant_order"


def test_notify_provider_lookup_failure_returns_502(client, monkeypatch):
    monkeypatch.delenv("MP_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ALLOW_INSECURE_DEV", "1")

    def boom(payment_id):
        raise mercadopago.MercadoPagoError("indisponível")

    monkeypatch.setattr(mercadopago, "get_payment", boom)
    response = client.post(
        "/api/webhooks/mercadopago/notify",
        json={"type": "payment", "data": {"id": "999"}},
    )
    assert response.status_code == 502


def test_notify_for_unknown_order_is_ignored(client, monkeypatch):
    monkeypatch.delenv("MP_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ALLOW_INSECURE_DEV", "1")
    monkeypatch.setattr(
        mercadopago,
        "get_payment",
        lambda payment_id: {"id": payment_id, "status": "approved", "external_reference": "nao-existe"},
    )
    response = client.post(
        "/api/webhooks/mercadopago/notify",
        json={"type": "payment", "data": {"id": "555"}},
    )
    assert response.status_code == 200
    assert response.json()["ignored"] == "ordem desconhecida"


def test_fulfill_order_is_idempotent_for_already_available_entitlement(client, monkeypatch, sent_emails):
    """Segunda compra do mesmo pacote não deve tentar reabrir uma entitlement já disponível."""
    order = client.post(
        "/api/checkout/order",
        json={"product_id": "site:mapa_astral", "email": "repete-fulfill@cliente.com", "locale": "es-AR"},
    ).json()
    monkeypatch.setattr(
        mercadopago,
        "create_payment",
        lambda **kwargs: {"id": 42, "status": "approved", "external_reference": kwargs["order_id"]},
    )
    payload = {"order_id": order["order_id"], "form_data": {"payment_method_id": "visa", "token": "t", "payer": {"email": "repete-fulfill@cliente.com"}}}
    first = client.post("/api/checkout/payment", json=payload)
    assert first.json()["approved"] is True

    order2 = client.post(
        "/api/checkout/order",
        json={"product_id": "site:mapa_astral", "email": "repete-fulfill@cliente.com", "locale": "es-AR"},
    ).json()
    monkeypatch.setattr(
        mercadopago,
        "create_payment",
        lambda **kwargs: {"id": 43, "status": "approved", "external_reference": kwargs["order_id"]},
    )
    payload2 = {"order_id": order2["order_id"], "form_data": {"payment_method_id": "visa", "token": "t", "payer": {"email": "repete-fulfill@cliente.com"}}}
    second = client.post("/api/checkout/payment", json=payload2)
    assert second.json()["approved"] is True
    assert len(sent_emails) == 2


def test_fulfill_order_reactivates_a_revoked_entitlement(client, monkeypatch, sent_emails):
    """Recompra de um produto com acesso revogado deve reativar, não duplicar, a entitlement."""
    from app.db import SessionLocal
    from app.models import Entitlement, User

    db = SessionLocal()
    try:
        user = User(email="revogado@cliente.com", password_hash="x", name="Revogado")
        db.add(user)
        db.flush()
        user_id = user.id
        db.add(Entitlement(user_id=user_id, product_id="site:mapa_astral", status="expired", source="site"))
        db.commit()
    finally:
        db.close()

    order = client.post(
        "/api/checkout/order",
        json={"product_id": "site:mapa_astral", "email": "revogado@cliente.com", "locale": "es-AR"},
    ).json()
    monkeypatch.setattr(
        mercadopago,
        "create_payment",
        lambda **kwargs: {"id": 7, "status": "approved", "external_reference": kwargs["order_id"]},
    )
    response = client.post(
        "/api/checkout/payment",
        json={"order_id": order["order_id"], "form_data": {"payment_method_id": "visa", "token": "t", "payer": {"email": "revogado@cliente.com"}}},
    )
    assert response.json()["approved"] is True

    db = SessionLocal()
    try:
        entitlement = db.scalar(
            select(Entitlement).where(Entitlement.user_id == user_id, Entitlement.product_id == "site:mapa_astral")
        )
        assert entitlement.status == "available"
    finally:
        db.close()


def test_generation_works_for_every_paid_content(client, monkeypatch):
    """Cliente fictício: compra, preenche o nascimento e gera cada leitura."""
    monkeypatch.setattr(checkout, "send_purchase_confirmation", lambda **kwargs: {"sent": True})
    register(client, "ficticia@example.com", "senha-segura-123", name="Cliente Fictícia", locale="es-AR")
    client.put(
        "/api/me/profile",
        json={
            "birth_date": "1992-11-03",
            "birth_time": "07:45:00",
            "birth_city": "Buenos Aires",
            "birth_country": "AR",
            "birth_timezone": "America/Argentina/Buenos_Aires",
            "partner_name": "Par Fictício",
            "partner_birth_date": "1990-02-17",
            "partner_birth_time": "18:10:00",
            "partner_birth_city": "Córdoba",
            "partner_country": "AR",
        },
    )
    for product_id in ("site:oferta_plano_lua_premium", "site:mapa_carreira"):
        client.post("/api/webhooks/cakto", json={"event_id": f"evt-{product_id}", "email": "ficticia@example.com", "product_id": product_id})

    contents = [
        "site:content:horoscopo_diario",
        "site:content:guia_do_mes",
        "site:content:mapa_astral_completo",
        "site:content:mapa_do_amor_sinastria",
        "site:content:mapa_da_carreira",
        "site:content:mapa_da_prosperidade",
        "site:content:previsao_semanal",
        "site:content:calendario_lunar",
        "site:content:guia_dos_retrogrados",
        "site:content:manual_do_ascendente",
    ]
    for content_id in contents:
        response = client.post(f"/api/me/readings/{content_id}/generate")
        assert response.status_code == 200, f"{content_id}: {response.text}"
        reading = response.json()["reading"]
        # In test env MINIMAX_API_KEY is unset, so every reading falls back
        # to the editorial template. The new contract surfaces this honestly.
        assert reading["status"] == "fallback"
        assert reading["source"] == "fallback"
        # Sectioned content_ids (e.g. mapa_astral_completo) render fallback as
        # <h2>/<h3>/<p> so the portal still shows section headers even when the
        # LLM is unavailable; other content_ids keep the plain <p> blob.
        assert reading["body_html"].startswith("<p>") or reading["body_html"].startswith("<h2>")

    listed = client.get("/api/me/readings").json()["readings"]
    assert len(listed) == len(contents)
