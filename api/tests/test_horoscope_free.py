"""O horóscopo do dia grátis é a boca do funil pago.

Duas propriedades sustentam a rota, e as duas quebram silenciosamente:

1. **Custo zero por visitante.** A rota é ilimitada e anônima. Um LLM aqui
   transformaria cada clique de anúncio em conta variável.
2. **Parecer escrito para aquela pessoa.** O que garante isso não é redação, é
   astronomia: duas pessoas no mesmo dia recebem textos diferentes porque os
   aspectos contra o mapa delas são diferentes.
"""

from datetime import date, timedelta

import pytest

from app import astrology, horoscope_free


@pytest.fixture
def geocoded(monkeypatch):
    # São Paulo. Geocoding real na suíte deixaria o teste dependente da rede e
    # da política de uso do Nominatim.
    monkeypatch.setattr(astrology, "resolve_coordinates", lambda city, country: (-23.55, -46.63))


BASE = {
    "name": "Mariana Alves",
    "birth_date": "1990-07-15",
    "birth_time": "14:30",
    "birth_city": "São Paulo",
    "birth_country": "BR",
    "birth_timezone": "America/Sao_Paulo",
}


def test_devolve_horoscopo_do_dia_com_o_nome_da_pessoa(client, geocoded):
    response = client.post("/api/horoscopo/gratis", json={**BASE, "locale": "pt-BR"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert "Mariana" in body["title"]
    assert "Mariana" in body["paragraphs"][0]
    # Sobrenome não entra: é assim que uma pessoa chama a outra.
    assert "Alves" not in body["body_html"]
    assert len(body["paragraphs"]) == 3
    assert body["date"] == date.today().isoformat()
    assert body["locked"] is True


def test_nao_chama_o_llm(client, geocoded, monkeypatch):
    """Visitante anônimo não pode virar custo variável."""
    from app import engine

    def explode(*args, **kwargs):
        raise AssertionError("o horóscopo grátis não pode chamar o LLM")

    for name in dir(engine):
        attribute = getattr(engine, name)
        if callable(attribute) and name.startswith("generate"):
            monkeypatch.setattr(engine, name, explode)

    assert client.post("/api/horoscopo/gratis", json={**BASE, "locale": "pt-BR"}).status_code == 200


def test_duas_pessoas_no_mesmo_dia_recebem_leituras_diferentes(client, geocoded):
    """A personalização é astronômica: mapas diferentes, aspectos diferentes."""
    uma = client.post("/api/horoscopo/gratis", json={**BASE, "locale": "pt-BR"}).json()
    outra = client.post(
        "/api/horoscopo/gratis",
        json={**BASE, "name": "Bruno", "birth_date": "1978-02-03", "birth_time": "05:10", "locale": "pt-BR"},
    ).json()

    assert uma["sun"]["sign"] != outra["sun"]["sign"]
    assert uma["body_html"] != outra["body_html"]


def test_mesma_pessoa_no_mesmo_dia_recebe_o_mesmo_texto(client, geocoded):
    """Determinismo: recarregar a página não pode reescrever o horóscopo."""
    primeira = client.post("/api/horoscopo/gratis", json={**BASE, "locale": "pt-BR"}).json()
    segunda = client.post("/api/horoscopo/gratis", json={**BASE, "locale": "pt-BR"}).json()

    assert primeira["body_html"] == segunda["body_html"]


def test_o_texto_muda_de_um_dia_para_o_outro(geocoded):
    """A Lua em trânsito é o relógio do dia — sem ela o texto viraria fixo."""
    from app.horoscope_free import HoroscopeBody, compose, natal_chart, strongest_aspect
    from datetime import datetime, timezone

    body = HoroscopeBody(**BASE, locale="pt-BR")
    natal = natal_chart(body, "pt-BR")

    textos = set()
    base = datetime(2026, 3, 1, 12, tzinfo=timezone.utc)
    for offset in (0, 3, 6, 9):
        momento = base + timedelta(days=offset)
        transitos = astrology._planet_positions(horoscope_free._julian_day(momento))
        aspecto = strongest_aspect(natal["points"], transitos)
        textos.add(compose("Mariana", natal, transitos, aspecto, "pt-BR")["body_html"])

    assert len(textos) > 1, "o horóscopo de hoje não pode ser igual ao de daqui a 9 dias"


@pytest.mark.parametrize(
    "locale,proibida",
    [("es-AR", "Hoje"), ("pt-BR", "Hoy")],
)
def test_nenhum_idioma_vaza_no_outro(client, geocoded, locale, proibida):
    body = client.post("/api/horoscopo/gratis", json={**BASE, "locale": locale}).json()
    assert proibida not in body["body_html"], body["body_html"]


def test_sem_hora_de_nascimento_avisa_que_o_ascendente_e_estimado(client, geocoded):
    payload = {key: value for key, value in BASE.items() if key != "birth_time"}
    body = client.post("/api/horoscopo/gratis", json={**payload, "locale": "es-AR"}).json()

    assert body["birth_time_assumed"] is True
    assert body["ascendant_warning"]
    assert "Ascendente" in body["ascendant_warning"]


def test_data_futura_e_recusada_na_lingua_do_visitante(client, geocoded):
    amanha = (date.today() + timedelta(days=1)).isoformat()
    response = client.post("/api/horoscopo/gratis", json={**BASE, "birth_date": amanha, "locale": "es-AR"})

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str), "o visitante nunca vê o array do Pydantic"


def test_cidade_desconhecida_devolve_erro_legivel(client, monkeypatch):
    monkeypatch.setattr(astrology, "resolve_coordinates", lambda city, country: None)
    response = client.post("/api/horoscopo/gratis", json={**BASE, "locale": "pt-BR"})

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)


def test_campo_faltando_devolve_frase_e_nao_array(client, geocoded):
    """A landing mostra o detail direto; array do Pydantic vazaria estrutura."""
    payload = {key: value for key, value in BASE.items() if key != "birth_city"}
    response = client.post("/api/horoscopo/gratis", json={**payload, "locale": "pt-BR"})

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)


def test_a_rota_e_limitada_por_ip(client, geocoded, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
    monkeypatch.setenv("RATE_LIMIT_PREVIEW_MAX", "2")
    monkeypatch.setenv("RATE_LIMIT_PREVIEW_WINDOW_SECONDS", "60")

    codigos = [
        client.post("/api/horoscopo/gratis", json={**BASE, "locale": "pt-BR"}).status_code
        for _ in range(4)
    ]

    assert 429 in codigos, codigos
