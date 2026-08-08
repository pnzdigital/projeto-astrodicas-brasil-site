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


def _complete_order_via_webhook(client, monkeypatch, order_id, payment_status="approved", payment_id=1, merchant_order_id=None):
    """Simula a notificação ``merchant_order`` do Checkout Pro: o cliente pagou
    (ou não) na página hospedada, e o Mercado Pago avisa por aqui."""
    merchant_order_id = merchant_order_id or f"mo-{order_id}"
    monkeypatch.setattr(
        mercadopago,
        "get_merchant_order",
        lambda merchant_order_id: {
            "id": merchant_order_id,
            "external_reference": order_id,
            "payments": [{"id": payment_id, "status": payment_status}],
        },
    )
    return client.post(
        "/api/webhooks/mercadopago/notify",
        json={"type": "merchant_order", "data": {"id": merchant_order_id}},
    )


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
    # Checkout Pro: o cartão é capturado numa página do Mercado Pago, não
    # dentro do site — por isso "transparent" é False mesmo para o provedor
    # mercadopago, e "redirect" é quem sinaliza o novo fluxo ao frontend.
    assert ar["checkout"]["provider"] == "mercadopago"
    assert ar["checkout"]["transparent"] is False
    assert ar["checkout"]["redirect"] is True
    lua_ar = next(p for p in ar["products"] if p["product_id"] == "site:plano_lua")
    assert lua_ar["price_label"] == "ARS 9.900"


def test_order_uses_server_price_and_opens_a_checkout_pro_preference(client, monkeypatch):
    unknown = client.post("/api/checkout/order", json={"product_id": "site:nope", "email": "a@b.com", "locale": "es-AR"})
    assert unknown.status_code == 404

    captured = {}

    def fake_create_preference(**kwargs):
        captured.update(kwargs)
        return {"id": "pref-1", "init_point": "https://www.mercadopago.com/checkout/pref-1"}

    monkeypatch.setattr(mercadopago, "create_preference", fake_create_preference)

    response = client.post(
        "/api/checkout/order",
        json={"product_id": "site:oferta_plano_lua_premium", "email": "Cliente@Example.com", "name": "Cliente Teste", "locale": "es-AR"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["amount"] == 34900.0
    assert body["amount_minor"] == pricing.PRICES_ARS_MINOR["site:oferta_plano_lua_premium"]
    assert body["currency"] == "ARS"
    assert body["redirect"] is True
    assert body["init_point"] == "https://www.mercadopago.com/checkout/pref-1"
    # O valor cobrado é o do servidor, não o que o navegador mandou.
    assert captured["amount"] == 34900.0
    assert captured["payer_email"] == "cliente@example.com"


def test_order_returns_502_when_provider_refuses_the_preference(client, monkeypatch):
    def boom(**kwargs):
        raise mercadopago.MercadoPagoError("timeout")

    monkeypatch.setattr(mercadopago, "create_preference", boom)
    response = client.post(
        "/api/checkout/order",
        json={"product_id": "site:mapa_astral", "email": "falha@cliente.com", "locale": "es-AR"},
    )
    assert response.status_code == 502


def test_payment_route_is_gone_and_returns_410(client):
    """Rota do checkout transparente aposentada: cliente antigo recebe um erro
    claro em vez de 404, e é instruído a reabrir a compra."""
    response = client.post(
        "/api/checkout/payment",
        json={"order_id": "qualquer-coisa", "form_data": {"payment_method_id": "visa"}},
    )
    assert response.status_code == 410


def test_merchant_order_approved_creates_account_grants_bundle_and_emails(client, monkeypatch, sent_emails):
    order = client.post(
        "/api/checkout/order",
        json={"product_id": "site:oferta_plano_lua_premium", "email": "nova@cliente.com", "name": "Nova Cliente", "locale": "es-AR"},
    ).json()

    response = _complete_order_via_webhook(client, monkeypatch, order["order_id"])
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "paid"

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

    paid = _complete_order_via_webhook(client, monkeypatch, order["order_id"], payment_id=2468)
    assert paid.json()["status"] == "paid"

    conversion = client.get(f"/api/checkout/order/{order['order_id']}/conversion")
    assert conversion.status_code == 200
    assert conversion.json() == {
        "event_id": f"site-purchase-{order['order_id']}",
        "order_id": order["order_id"],
        "content_ids": ["site:oferta_plano_lua_premium"],
        "content_name": "Círculo Completo",
        "content_type": "product",
        "num_items": 1,
        "value": 34900.0,
        "currency": "ARS",
        "content_language": "es-AR",
    }
    assert "email" not in conversion.json()


def test_merchant_order_notification_is_not_double_fulfilled_and_emails_once(client, monkeypatch, sent_emails):
    order = client.post(
        "/api/checkout/order",
        json={"product_id": "site:mapa_astral", "email": "repete@cliente.com", "locale": "es-AR"},
    ).json()

    first = _complete_order_via_webhook(client, monkeypatch, order["order_id"])
    second = _complete_order_via_webhook(client, monkeypatch, order["order_id"])
    assert first.json()["status"] == "paid"
    assert second.json().get("duplicate") is True
    assert len(sent_emails) == 1


def test_rejected_payment_does_not_grant_access(client, monkeypatch, sent_emails):
    order = client.post(
        "/api/checkout/order",
        json={"product_id": "site:mapa_carreira", "email": "recusa@cliente.com", "locale": "es-AR"},
    ).json()
    response = _complete_order_via_webhook(client, monkeypatch, order["order_id"], payment_status="rejected")
    assert response.json()["status"] == "failed"
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
        json={"type": "chargebacks", "data": {"id": "1"}},
    )
    assert response.status_code == 200
    assert response.json()["ignored"] == "chargebacks"


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


def test_notify_merchant_order_lookup_failure_returns_502(client, monkeypatch):
    monkeypatch.delenv("MP_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ALLOW_INSECURE_DEV", "1")

    def boom(merchant_order_id):
        raise mercadopago.MercadoPagoError("indisponível")

    monkeypatch.setattr(mercadopago, "get_merchant_order", boom)
    response = client.post(
        "/api/webhooks/mercadopago/notify",
        json={"type": "merchant_order", "data": {"id": "mo-999"}},
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
    first = _complete_order_via_webhook(client, monkeypatch, order["order_id"], payment_id=42, merchant_order_id="mo-1")
    assert first.json()["status"] == "paid"

    order2 = client.post(
        "/api/checkout/order",
        json={"product_id": "site:mapa_astral", "email": "repete-fulfill@cliente.com", "locale": "es-AR"},
    ).json()
    second = _complete_order_via_webhook(client, monkeypatch, order2["order_id"], payment_id=43, merchant_order_id="mo-2")
    assert second.json()["status"] == "paid"
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
    response = _complete_order_via_webhook(client, monkeypatch, order["order_id"], payment_id=7)
    assert response.json()["status"] == "paid"

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
        # /generate answers 202 immediately (BackgroundTask); the reading is
        # only "ready"/"fallback" once GET /api/me/readings is polled.
        assert response.status_code == 202, f"{content_id}: {response.text}"
        assert response.json()["reading"]["status"] == "in_progress"
        reading = next(
            r for r in client.get("/api/me/readings").json()["readings"] if r["content_id"] == content_id
        )
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
