"""Estado/provincia de nascimento como desambiguador de cidade homônima.

"Santa Cruz" existe em vários estados do Brasil; sem o estado, o geocoding
offline não tem como saber qual. Este arquivo trava resolve_coordinates()
diretamente (sem passar pela rota) e a rota /api/horoscopo/gratis com
requisições reais, incluindo a legada (sem o campo, comportamento atual
não pode quebrar).
"""

import pytest

from app import astrology


@pytest.fixture(autouse=True)
def geocoding_enabled(monkeypatch):
    # conftest desliga GEOCODING_ENABLED globalmente (suíte não pode bater na
    # rede); este arquivo testa a resolução offline em si, então precisa
    # ligar de volta só aqui.
    monkeypatch.setenv("GEOCODING_ENABLED", "1")


BASE = {
    "name": "Mariana Alves",
    "birth_date": "1990-07-15",
    "birth_time": "14:30",
    "birth_city": "São Paulo",
    "birth_country": "BR",
    "birth_timezone": "America/Sao_Paulo",
}


def test_petrolina_pe_resolve_offline():
    assert astrology.resolve_coordinates("Petrolina", "BR", "PE") is not None
    assert astrology.resolve_coordinates("Petrolina", "BR") is not None


def test_santa_cruz_em_estados_diferentes_resolve_coordenadas_diferentes():
    rn = astrology.resolve_coordinates("Santa Cruz", "BR", "RN")
    pe = astrology.resolve_coordinates("Santa Cruz", "BR", "PE")

    assert rn is not None and pe is not None
    assert rn != pe


def test_santa_cruz_sem_estado_ainda_cai_no_comportamento_legado(monkeypatch):
    """Sem estado, a tabela por-estado não é nem consultada: comportamento
    idêntico ao de antes do campo existir (tabela plana, depois Nominatim)."""
    chamadas = []
    monkeypatch.setattr(astrology, "_geocode_online", lambda city, country, state=None: chamadas.append((city, country, state)) or None)

    result = astrology.resolve_coordinates("Santa Cruz", "BR")

    assert result is None
    assert chamadas == [("Santa Cruz", "BR", None)]


def test_cordoba_ar_nao_geocodifica_no_brasil(monkeypatch):
    # A tabela offline por país nunca cruza fronteira: "Córdoba" no país "BR"
    # não deve nem chegar perto da entrada argentina. O fallback online é
    # mockado para não depender de rede no teste.
    monkeypatch.setattr(astrology, "_geocode_online", lambda city, country, state=None: None)

    assert astrology.resolve_coordinates("Córdoba", "AR", "Córdoba") is not None
    assert astrology.resolve_coordinates("Córdoba", "BR") is None


def test_santa_fe_ar_nao_geocodifica_no_brasil(monkeypatch):
    monkeypatch.setattr(astrology, "_geocode_online", lambda city, country, state=None: None)

    assert astrology.resolve_coordinates("Santa Fe", "AR") is not None
    assert astrology.resolve_coordinates("Santa Fe", "BR") is None


def test_estado_invalido_nao_quebra_cai_no_fallback(monkeypatch):
    """Estado que não bate com nenhuma sigla/provincia conhecida não derruba a
    request: só não desambigua, e o resto do pipeline (tabela plana depois
    Nominatim) segue rodando normalmente."""
    monkeypatch.setattr(astrology, "_geocode_online", lambda city, country, state=None: None)

    result = astrology.resolve_coordinates("Petrolina", "BR", "ZZ-estado-que-nao-existe")

    assert result is not None  # cai na tabela plana, que tem Petrolina


def test_rota_gratis_aceita_requisicao_legada_sem_estado(client, monkeypatch):
    monkeypatch.setattr(astrology, "resolve_coordinates", lambda city, country, state=None: (-23.55, -46.63))

    response = client.post("/api/horoscopo/gratis", json={**BASE, "locale": "pt-BR"})

    assert response.status_code == 200, response.text


def test_rota_gratis_com_estado_desambigua_cidade_homonima(client):
    payload_rn = {**BASE, "birth_city": "Santa Cruz", "birth_state": "RN"}
    payload_pe = {**BASE, "birth_city": "Santa Cruz", "birth_state": "PE"}

    resposta_rn = client.post("/api/horoscopo/gratis", json={**payload_rn, "locale": "pt-BR"})
    resposta_pe = client.post("/api/horoscopo/gratis", json={**payload_pe, "locale": "pt-BR"})

    assert resposta_rn.status_code == 200, resposta_rn.text
    assert resposta_pe.status_code == 200, resposta_pe.text


def test_rota_gratis_com_estado_invalido_nao_quebra(client):
    payload = {**BASE, "birth_state": "estado-inexistente"}

    response = client.post("/api/horoscopo/gratis", json={**payload, "locale": "pt-BR"})

    assert response.status_code == 200, response.text
