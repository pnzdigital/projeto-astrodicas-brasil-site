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


def test_preview_without_birth_time_assumes_midnight_and_estimates_ascendant(client, geocoded):
    """Sem hora de nascimento, a prévia mantém o Ascendente (assumindo 00:00)
    e marca ``birth_time_assumed``. O aviso localizado avisa que o Ascendente
    é估计ado e provavelmente muda se a hora real for outra — o dado do mapa
    mais sensível à hora."""
    payload = {key: value for key, value in BIRTH.items() if key != "birth_time"}

    data = client.post("/api/preview/natal", json=payload).json()

    assert data["birth_time_assumed"] is True
    assert data["ascendant"] is not None, "Assumir 00:00 → Ascendente não pode voltar null"
    assert data["ascendant"]["sign"] in astrology.SIGNS
    assert data["ascendant"]["text"]
    assert data["sun"]["text"]
    assert data["moon"]["text"]
    warning_pt = data["ascendant_warning"]["pt-BR"]
    assert "Ascendente" in warning_pt or "ascendente" in warning_pt.lower()
    assert any(
        token in warning_pt.lower()
        for token in ("estim", "aproxim", "hipotét", "provavelmente")
    ), f"Aviso PT não transmite incerteza: {warning_pt!r}"
    warning_es = data["ascendant_warning"]["es-AR"]
    assert warning_es != warning_pt, "Aviso precisa ser localizado, não duplicado"


def test_preview_with_birth_time_does_not_warn(client, geocoded):
    """Com hora informada, o Ascendente é calculado normalmente e a resposta
    não traz aviso: o visitante não precisa ser alertado de algo que não
    aconteceu."""
    data = client.post("/api/preview/natal", json=BIRTH).json()

    assert data["birth_time_assumed"] is False
    assert data["ascendant"] is not None
    assert data["ascendant"]["sign"] in astrology.SIGNS
    assert "ascendant_warning" not in data or not data["ascendant_warning"]


def test_preview_assumed_time_uses_midnight_for_calculation(client, geocoded, monkeypatch):
    """Garante que a hora assumida é 00:00 do fuso local, não 12:00 (que era o
    default silencioso). Spy em ``swe.julday`` para capturar o decimal_hour."""
    captured = {}

    import swisseph as swe

    def spy(jyear, jmonth, jday, decimal_hour, *args, **kwargs):
        captured["decimal_hour"] = decimal_hour
        captured["date"] = (jyear, jmonth, jday)
        return swe.julday(jyear, jmonth, jday, decimal_hour, *args, **kwargs)

    monkeypatch.setattr(swe, "julday", spy)

    payload = {key: value for key, value in BIRTH.items() if key != "birth_time"}
    client.post("/api/preview/natal", json=payload)

    # 20/05/1990 em America/Recife (UTC-03) à meia-noite local = 03:00 UTC.
    assert captured["decimal_hour"] == pytest.approx(3.0, abs=1e-6), (
        f"hora assumida deveria ser 00:00 local → 03:00 UTC, veio {captured['decimal_hour']!r}"
    )


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
