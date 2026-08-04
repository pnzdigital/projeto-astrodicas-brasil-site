"""Caminhos de erro e casos de borda de main.py não cobertos pelos testes de fluxo feliz."""


def test_health_endpoint(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "service": "astrodicas-site", "channel": "site"}


def test_register_duplicate_email_returns_neutral_200(client):
    """Anti-enumeração: o segundo cadastro com o mesmo e-mail recebe o mesmo
    status (200) do cadastro novo, com ``created: false`` e sem cookie."""
    payload = {"email": "duplicada@example.com", "password": "senha-segura", "name": "Primeira"}
    first = client.post("/api/auth/register", json=payload)
    assert first.status_code == 200
    assert first.json()["created"] is True
    assert "site_session" in first.cookies

    second = client.post("/api/auth/register", json={**payload, "name": "Segunda"})
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert "site_session" not in second.cookies


def test_login_wrong_password_returns_401(client):
    client.post("/api/auth/register", json={"email": "senha@example.com", "password": "senha-correta", "name": "Ana"})
    response = client.post("/api/auth/login", json={"email": "senha@example.com", "password": "senha-errada"})
    assert response.status_code == 401


def test_login_success_sets_session(client):
    client.post("/api/auth/register", json={"email": "login-ok@example.com", "password": "senha-correta", "name": "Ana"})
    client.post("/api/auth/logout")
    response = client.post("/api/auth/login", json={"email": "login-ok@example.com", "password": "senha-correta"})
    assert response.status_code == 200
    assert response.json()["user"]["email"] == "login-ok@example.com"
    assert client.get("/api/session").json()["authenticated"] is True


def test_login_unknown_email_returns_401(client):
    response = client.post("/api/auth/login", json={"email": "nao-existe@example.com", "password": "qualquer"})
    assert response.status_code == 401


def test_profile_without_data_returns_none(client):
    client.post("/api/auth/register", json={"email": "sem-perfil@example.com", "password": "senha-segura", "name": "Sem Perfil"})
    response = client.get("/api/me/profile")
    assert response.status_code == 200
    assert response.json() == {"profile": None}


def test_access_without_entitlements_is_empty(client):
    client.post("/api/auth/register", json={"email": "sem-acesso@example.com", "password": "senha-segura", "name": "Sem Acesso"})
    response = client.get("/api/me/access")
    assert response.status_code == 200
    assert response.json() == {"entitlements": []}


def test_readings_list_is_empty_before_any_generation(client):
    client.post("/api/auth/register", json={"email": "sem-leitura@example.com", "password": "senha-segura", "name": "Sem Leitura"})
    response = client.get("/api/me/readings")
    assert response.status_code == 200
    assert response.json() == {"readings": []}


def test_protected_routes_require_session(client):
    assert client.get("/api/me/profile").status_code == 401
    assert client.get("/api/me/access").status_code == 401
    assert client.get("/api/me/readings").status_code == 401
    assert client.post("/api/me/readings/site:content:horoscopo_diario/generate").status_code == 401


def test_generate_paid_content_without_entitlement_returns_403(client):
    client.post(
        "/api/auth/register",
        json={"email": "sem-entitlement@example.com", "password": "senha-segura", "name": "Sem Entitlement"},
    )
    client.put(
        "/api/me/profile",
        json={"birth_date": "1990-05-20", "birth_time": "12:30:00", "birth_city": "Recife", "birth_country": "BR"},
    )
    response = client.post("/api/me/readings/site:content:mapa_astral_completo/generate")
    assert response.status_code == 403


def test_webhook_unknown_provider_returns_404(client):
    response = client.post(
        "/api/webhooks/stripe",
        json={"event_id": "evt-1", "email": "a@b.com", "product_id": "site:plano_lua"},
    )
    assert response.status_code == 404
