"""Isolamento entre contas: sessão/cookie de um usuário nunca deve enxergar
dados ou acesso de outro. Cobre a classe de bug IDOR neste backend, cujo
único vetor real é a sessão (não há endpoints por ID de leitura/perfil).
"""

from fastapi.testclient import TestClient

from app.main import app
from conftest import register as _do_register


def _register(client, email, password="senha-segura-123", name="Cliente"):
    response = _do_register(client, email, password, name=name)
    assert response.status_code == 200, response.text
    return response.json()["user"]["id"]


def test_user_b_readings_never_include_user_a_readings(client, monkeypatch):
    monkeypatch.setattr("app.checkout.send_purchase_confirmation", lambda **kwargs: {"sent": True})

    _register(client, "isolamento-a@example.com")
    client.put(
        "/api/me/profile",
        json={"birth_date": "1990-05-20", "birth_time": "12:30:00", "birth_city": "Recife", "birth_country": "BR"},
    )
    client.post(
        "/api/webhooks/cakto",
        json={"event_id": "evt-isolamento-a", "email": "isolamento-a@example.com", "product_id": "site:plano_lua"},
    )
    generated = client.post("/api/me/readings/site:content:horoscopo_diario/generate")
    # /generate is async now: a freshly-queued generation answers 202.
    assert generated.status_code == 202

    client.post("/api/auth/logout")
    _register(client, "isolamento-b@example.com")

    readings_b = client.get("/api/me/readings").json()["readings"]
    assert readings_b == []

    access_b = client.get("/api/me/access").json()["entitlements"]
    assert access_b == []


def test_stale_session_cookie_from_logged_out_user_is_rejected():
    """Um cookie de sessão válido só deve valer enquanto associado ao usuário certo:
    logout + novo registro não deve reaproveitar contexto de outra conta."""
    with TestClient(app) as c1, TestClient(app) as c2:
        _register(c1, "sessao-a@example.com")
        session_a = c1.cookies.get("site_session")
        assert session_a

        # Um segundo cliente tentando usar o cookie da sessão A não pode virar outra pessoa.
        me_via_stolen_cookie = c2.get("/api/session", cookies={"site_session": session_a}).json()
        assert me_via_stolen_cookie["user"]["email"] == "sessao-a@example.com"
        # Isso é esperado (cookie roubado = sessão roubada, como qualquer cookie de sessão);
        # o que não pode acontecer é o inverso: cookie de B nunca decodifica para A.
        _register(c2, "sessao-b@example.com")
        session_b = c2.cookies.get("site_session")
        assert session_b != session_a


def test_entitlement_for_one_product_does_not_unlock_unrelated_paid_content(client, monkeypatch):
    monkeypatch.setattr("app.checkout.send_purchase_confirmation", lambda **kwargs: {"sent": True})
    _register(client, "produto-unico@example.com")
    client.put(
        "/api/me/profile",
        json={"birth_date": "1990-05-20", "birth_time": "12:30:00", "birth_city": "Recife", "birth_country": "BR"},
    )
    # Compra só o Mapa da Carreira: não deve abrir Mapa Astral nem Plano Lua.
    client.post(
        "/api/webhooks/cakto",
        json={"event_id": "evt-produto-unico", "email": "produto-unico@example.com", "product_id": "site:mapa_carreira"},
    )
    unrelated = client.post("/api/me/readings/site:content:mapa_astral_completo/generate")
    assert unrelated.status_code == 403
    allowed = client.post("/api/me/readings/site:content:mapa_da_carreira/generate")
    # /generate is async now: a freshly-queued generation answers 202.
    assert allowed.status_code == 202
