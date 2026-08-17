from datetime import date

import pytest

from app import engine
from app.models import Profile


KINDS = {
    "site:content:horoscopo_diario": "daily",
    "site:content:mapa_do_amor_sinastria": "synastry",
    "site:content:mapa_da_carreira": "career",
    "site:content:mapa_astral_completo": "full_map",
    "site:content:mapa_da_prosperidade": "prosperity",
    "site:content:manual_do_ascendente": "love",
}


@pytest.fixture
def profile():
    return Profile(user_id="user-1", birth_date=date(1990, 5, 10), birth_city="Recife", partner_name="Joana", partner_birth_date=date(1992, 8, 20))


def test_each_kind_has_unique_text(profile, monkeypatch):
    monkeypatch.setattr(engine, "_call_minimax", lambda prompt: (_ for _ in ()).throw(RuntimeError("down")))
    results = [engine.generate_reading(content_id, "Título", profile, customer_name="Lia", user_id="user-1") for content_id in KINDS]
    assert {result["reading_kind"] for result in results} == set(KINDS.values())
    assert len({result["text"] for result in results}) == len(results)


def test_fallback_contains_name_sign_and_synastry_partner(profile, monkeypatch):
    monkeypatch.setattr(engine, "_call_minimax", lambda prompt: (_ for _ in ()).throw(RuntimeError("down")))
    fallback = engine.generate_reading("site:content:mapa_do_amor_sinastria", "Sinastria", profile, customer_name="Lia", user_id="user-1")
    assert fallback["source"] == "fallback"
    assert "Lia" in fallback["text"]
    assert "Touro" in fallback["text"]
    assert "Joana" in fallback["text"]


def test_success_path_returns_llm(profile, monkeypatch):
    monkeypatch.setattr(engine, "_call_minimax", lambda prompt: "Primeiro parágrafo.\n\nSegundo parágrafo.\n\nTerceiro parágrafo.")
    result = engine.generate_reading("site:content:horoscopo_diario", "Diário", profile, customer_name="Lia", user_id="user-1")
    assert result["source"] == "llm"
    assert result["reading_kind"] == "daily"
    assert result["text"].count("<p>") == 3
    assert result["generated_at"]


def test_empty_minimax_returns_fallback(profile, monkeypatch):
    monkeypatch.setattr(engine, "_call_minimax", lambda prompt: "")
    result = engine.generate_reading("site:content:mapa_da_carreira", "Carreira", profile, customer_name="Lia", user_id="user-1")
    assert result["source"] == "fallback"
    assert "Lia" in result["text"]
    assert "Touro" in result["text"]
