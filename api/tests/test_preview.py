"""Prévia grátis do mapa natal: POST /api/preview/natal.

Contrato comercial da rota: é a única porta pública do produto. Ela entrega
Sol, Lua, Ascendente e as posições planetárias sem login e sem pagamento, e
NÃO entrega a leitura paga (casas, aspectos, texto gerado). Os testes abaixo
travam esses dois lados: o que precisa vir, e o que nunca pode vazar.
"""

from __future__ import annotations

import pytest

from app import astrology, ratelimit


BIRTH = {
    "birth_date": "1990-05-20",
    "birth_time": "14:30",
    "birth_city": "Recife",
    "birth_country": "BR",
    "birth_timezone": "America/Recife",
}


@pytest.fixture()
def geocoded(monkeypatch):
    """Geocoding é desligado no conftest; aqui fixamos coordenadas reais de
    Recife para exercitar o caminho feliz sem tocar a rede."""
    monkeypatch.setattr(astrology, "resolve_coordinates", lambda city, country: (-8.0476, -34.877))
    yield


def test_preview_returns_sun_moon_ascendant_without_authentication(client, geocoded):
    response = client.post("/api/preview/natal", json=BIRTH)

    assert response.status_code == 200, response.text
    data = response.json()
    for luminary in ("sun", "moon", "ascendant"):
        assert data[luminary]["sign"] in astrology.SIGNS
        assert isinstance(data[luminary]["degree"], float)
        assert len(data[luminary]["text"]) > 60, f"{luminary} sem parágrafo de verdade"
    # Sol de 20/05/1990 fica em Touro (entra em Gêmeos só depois de 21/05).
    assert data["sun"]["sign"] == "Touro"


def test_preview_lists_planet_positions(client, geocoded):
    data = client.post("/api/preview/natal", json=BIRTH).json()

    names = [planet["name"] for planet in data["planets"]]
    assert names == [name for name, _ in astrology.PLANETS]
    for planet in data["planets"]:
        assert planet["sign"] in astrology.SIGNS
        assert isinstance(planet["retrograde"], bool)


def test_preview_never_returns_the_paid_reading(client, geocoded):
    response = client.post("/api/preview/natal", json=BIRTH)
    data = response.json()

    assert data["locked"] is True
    for paid_key in ("body_html", "houses", "natal_aspects", "aspects", "transits_to_natal", "current_sky", "reading"):
        assert paid_key not in data, f"prévia grátis vazou conteúdo pago: {paid_key}"
    assert "<p>" not in response.text


def test_preview_without_birth_time_omits_ascendant(client, geocoded):
    payload = {key: value for key, value in BIRTH.items() if key != "birth_time"}

    data = client.post("/api/preview/natal", json=payload).json()

    assert data["ascendant"] is None
    assert data["birth_time_approximate"] is True
    assert data["sun"]["text"]
    assert data["moon"]["text"]


def test_preview_texts_are_localized_for_es_ar(client, geocoded):
    portuguese = client.post("/api/preview/natal", json=BIRTH).json()
    spanish = client.post("/api/preview/natal", json={**BIRTH, "locale": "es-AR"}).json()

    assert spanish["locale"] == "es-AR"
    assert spanish["sun"]["sign_label"] == "Tauro"
    assert portuguese["sun"]["sign_label"] == "Touro"
    assert spanish["sun"]["text"] != portuguese["sun"]["text"]


def test_preview_unknown_city_returns_localized_error(client, monkeypatch):
    monkeypatch.setattr(astrology, "resolve_coordinates", lambda city, country: None)

    response = client.post("/api/preview/natal", json={**BIRTH, "birth_city": "Zzzz Inexistente"})
    assert response.status_code == 422
    assert "cidade" in response.json()["detail"].lower()

    spanish = client.post(
        "/api/preview/natal",
        json={**BIRTH, "birth_city": "Zzzz Inexistente", "locale": "es-AR"},
    )
    assert spanish.status_code == 422
    assert "ciudad" in spanish.json()["detail"].lower()


def test_preview_invalid_date_returns_localized_error(client, geocoded):
    response = client.post("/api/preview/natal", json={**BIRTH, "birth_date": "1990-13-45"})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, str), "erro de validação não pode vazar o array do Pydantic"
    assert "data" in detail.lower()


def test_preview_rejects_future_birth_date(client, geocoded):
    response = client.post("/api/preview/natal", json={**BIRTH, "birth_date": "2999-01-01"})

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)


def test_preview_is_rate_limited(client, monkeypatch, geocoded):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
    monkeypatch.setenv("RATE_LIMIT_PREVIEW_MAX", "2")
    monkeypatch.setenv("RATE_LIMIT_PREVIEW_WINDOW_SECONDS", "60")
    ratelimit.reset_all()

    for _ in range(2):
        assert client.post("/api/preview/natal", json=BIRTH).status_code == 200
    blocked = client.post("/api/preview/natal", json=BIRTH)

    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0
    ratelimit.reset_all()


def test_preview_does_not_call_the_llm(client, monkeypatch, geocoded):
    """A prévia é grátis e ilimitada: chamar a MiniMax aqui viraria custo por
    visitante. Se alguém plugar o engine nesta rota, este teste quebra."""
    from app import engine

    def explode(*args, **kwargs):  # pragma: no cover - só roda se houver regressão
        raise AssertionError("prévia grátis não pode chamar a LLM")

    monkeypatch.setattr(engine, "_call_minimax", explode)
    monkeypatch.setattr(engine, "generate_reading", explode)

    assert client.post("/api/preview/natal", json=BIRTH).status_code == 200
