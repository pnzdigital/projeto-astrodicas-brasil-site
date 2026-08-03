"""CORS: só as origens declaradas em SITE_ORIGIN podem receber credenciais."""


def test_allowed_origin_gets_cors_headers(client):
    response = client.get("/api/health", headers={"Origin": "http://testserver"})
    assert response.headers.get("access-control-allow-origin") == "http://testserver"
    assert response.headers.get("access-control-allow-credentials") == "true"


def test_unlisted_origin_does_not_get_cors_headers(client):
    response = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers
