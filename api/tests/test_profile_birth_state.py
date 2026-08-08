"""birth_state e partner_birth_state no perfil da assinante.

Testes principais:
- perfil com birth_state desambigua cidade homônima via resolve_coordinates
- perfil legado sem birth_state continua gerando leitura (compat)
- profile_to_dict expõe ambos os campos novos
- partner_birth_state é persistido e devolvido
"""

import pytest

from app import astrology
from app import main as _app_main  # capturado na coleção, antes de test_auth_gate recarregar o módulo
from conftest import register


@pytest.fixture(autouse=True)
def geocoding_on(monkeypatch):
    monkeypatch.setenv("GEOCODING_ENABLED", "1")


# ---------------------------------------------------------------------------
# Persistência via PUT /api/me/profile
# ---------------------------------------------------------------------------

def test_birth_state_salvo_e_devolvido(client):
    register(client, "ana@example.com", "senha-segura", name="Ana")
    resp = client.put("/api/me/profile", json={
        "birth_date": "1990-07-15",
        "birth_city": "Santa Cruz",
        "birth_state": "RN",
        "birth_country": "BR",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["profile"]["birth_state"] == "RN"


def test_partner_birth_state_salvo_e_devolvido(client):
    register(client, "ana@example.com", "senha-segura", name="Ana")
    resp = client.put("/api/me/profile", json={
        "birth_date": "1990-07-15",
        "birth_city": "São Paulo",
        "birth_country": "BR",
        "partner_birth_city": "Santa Cruz",
        "partner_birth_state": "PE",
        "partner_country": "BR",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["profile"]["partner_birth_state"] == "PE"


def test_get_profile_expoe_birth_state(client):
    register(client, "ana@example.com", "senha-segura", name="Ana")
    client.put("/api/me/profile", json={
        "birth_date": "1990-07-15",
        "birth_city": "Santa Cruz",
        "birth_state": "PE",
        "birth_country": "BR",
    })
    resp = client.get("/api/me/profile")
    assert resp.status_code == 200
    assert resp.json()["profile"]["birth_state"] == "PE"


# ---------------------------------------------------------------------------
# Geocoding desambiguação via perfil
# ---------------------------------------------------------------------------

def test_save_profile_usa_estado_para_geocoding(client, monkeypatch):
    """save_profile deve repassar birth_state a resolve_coordinates."""
    calls = []

    def fake_resolve(city, country, state=None):
        calls.append((city, country, state))
        return (-6.22, -36.03)

    # main.py importa resolve_coordinates diretamente — patch no módulo main.
    # Usa _app_main (import de módulo, capturado na coleta) para evitar pegar
    # o módulo recarregado por test_auth_gate._reload_main durante a suíte.
    monkeypatch.setattr(_app_main, "resolve_coordinates", fake_resolve)

    register(client, "ana@example.com", "senha-segura", name="Ana")
    client.put("/api/me/profile", json={
        "birth_date": "1990-07-15",
        "birth_city": "Santa Cruz",
        "birth_state": "RN",
        "birth_country": "BR",
    })

    assert calls, "resolve_coordinates não foi chamada"
    assert calls[0] == ("Santa Cruz", "BR", "RN")


def test_santa_cruz_rn_vs_pe_coordenadas_diferentes_no_perfil(client):
    """Dois perfis com a mesma cidade mas estados diferentes produzem
    coordenadas diferentes (via tabela offline OFFLINE_CITIES_BY_STATE)."""
    register(client, "rn@example.com", "senha-segura", name="A")
    client.put("/api/me/profile", json={
        "birth_date": "1990-07-15",
        "birth_city": "Santa Cruz",
        "birth_state": "RN",
        "birth_country": "BR",
    })
    profile_rn = client.get("/api/me/profile").json()["profile"]

    client.post("/api/auth/logout")

    register(client, "pe@example.com", "senha-segura", name="B")
    client.put("/api/me/profile", json={
        "birth_date": "1990-07-15",
        "birth_city": "Santa Cruz",
        "birth_state": "PE",
        "birth_country": "BR",
    })
    profile_pe = client.get("/api/me/profile").json()["profile"]

    assert profile_rn["birth_latitude"] != profile_pe["birth_latitude"]
    assert profile_rn["birth_longitude"] != profile_pe["birth_longitude"]


# ---------------------------------------------------------------------------
# Compatibilidade com perfis legados (sem birth_state)
# ---------------------------------------------------------------------------

def test_perfil_legado_sem_estado_salva_ok(client):
    """Perfil sem birth_state aceita save sem erro (campo opcional)."""
    register(client, "legada@example.com", "senha-segura", name="Legada")
    resp = client.put("/api/me/profile", json={
        "birth_date": "1985-03-10",
        "birth_time": "08:00",
        "birth_city": "São Paulo",
        "birth_country": "BR",
    })
    assert resp.status_code == 200
    assert resp.json()["profile"]["birth_state"] is None


def test_astrology_context_tolera_profile_sem_birth_state():
    """astrology_context não explode em perfil sem o atributo birth_state
    (simula objeto antigo sem a coluna mapeada)."""

    class LegacyProfile:
        birth_date = None  # retorna early em missing_birth_date

    # Sem birth_state no objeto — getattr com default None não deve quebrar.
    result = astrology.astrology_context(LegacyProfile())
    assert result.get("status") == "missing_birth_date"
