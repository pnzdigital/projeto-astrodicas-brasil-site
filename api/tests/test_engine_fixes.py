"""Regression tests for the 5 bugs found in engine/astrology on 2026-08-04.

Each test maps to a real defect or risk:
  - partner_birth_country / partner_birth_timezone not in LLM prompt context
  - silent house loss when geocoding fails (no server-detectable status)
  - no latitude/longitude range validation before swe.houses_ex
  - geocoding is a blocking external call inside the astrology path
  - max_tokens=2400 truncates premium 10-14 paragraph readings
  - fallback reading is presented as if it were the paid personalized reading
"""

from __future__ import annotations

from datetime import date, time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app import astrology, engine


class _Profile:
    def __init__(self, **kwargs):
        self.birth_date = kwargs.get("birth_date", date(1990, 5, 20))
        self.birth_time = kwargs.get("birth_time", time(10, 30))
        self.birth_city = kwargs.get("birth_city", "São Paulo")
        self.birth_country = kwargs.get("birth_country", "BR")
        self.birth_timezone = kwargs.get("birth_timezone", "America/Sao_Paulo")
        self.birth_latitude = kwargs.get("birth_latitude", None)
        self.birth_longitude = kwargs.get("birth_longitude", None)
        self.partner_name = kwargs.get("partner_name", "Maya")
        self.partner_birth_date = kwargs.get("partner_birth_date", date(1992, 7, 14))
        self.partner_birth_time = kwargs.get("partner_birth_time", None)
        self.partner_birth_city = kwargs.get("partner_birth_city", "Rio de Janeiro")
        self.partner_country = kwargs.get("partner_country", "BR")


# ---------------------------------------------------------------------------
# Bug 1: partner country / timezone missing from the LLM prompt context
# ---------------------------------------------------------------------------

def test_partner_country_and_timezone_in_profile_context():
    profile = _Profile()
    profile.partner_country = "PT"
    profile.partner_birth_timezone = "Europe/Lisbon"
    ctx = engine._profile_context(profile, "Alex")
    # LLM must see partner country and timezone so it can interpret partner birth
    assert "partner_birth_country" in ctx
    assert ctx["partner_birth_country"] == "PT"
    assert "partner_birth_timezone" in ctx
    assert ctx["partner_birth_timezone"] == "Europe/Lisbon"


def test_partner_country_defaults_to_uninformado_when_missing():
    profile = _Profile()
    profile.partner_country = ""
    profile.partner_birth_timezone = ""
    ctx = engine._profile_context(profile, "")
    assert ctx["partner_birth_country"] == "não informado"
    assert ctx["partner_birth_timezone"] == "não informado"


# ---------------------------------------------------------------------------
# Bug 2: latitude/longitude range validation before swe.houses_ex
# ---------------------------------------------------------------------------

def test_astrology_context_rejects_out_of_range_latitude():
    profile = _Profile(birth_latitude="999.0", birth_longitude="0.0")
    chart = astrology.astrology_context(profile)
    # Polar / invalid lat must not crash; chart must signal it explicitly
    assert chart.get("status") == "invalid_coordinates"
    assert chart.get("houses_status") == "invalid_coordinates"
    assert "latitude" in chart.get("coordinate_error", "")


def test_astrology_context_rejects_out_of_range_longitude():
    profile = _Profile(birth_latitude="0.0", birth_longitude="-999.0")
    chart = astrology.astrology_context(profile)
    assert chart.get("status") == "invalid_coordinates"
    assert chart.get("houses_status") == "invalid_coordinates"


def test_astrology_context_marks_invalid_coords_without_crashing():
    profile = _Profile(birth_latitude="999", birth_longitude="999")
    chart = astrology.astrology_context(profile)
    # Out-of-range coordinates must short-circuit cleanly with a chart-level
    # error status; the API layer must not 500.
    assert chart.get("status") == "invalid_coordinates"
    assert chart.get("houses_status") == "invalid_coordinates"
    assert "latitude" in chart.get("coordinate_error", "") or "longitude" in chart.get("coordinate_error", "")
    # No planet positions lie to the caller when location is invalid
    assert "planets" not in chart


# ---------------------------------------------------------------------------
# Bug 3: silent house loss when geocoding fails — must be server-detectable
# ---------------------------------------------------------------------------

def test_astrology_context_marks_geocoding_failure(monkeypatch):
    """No stored lat/lon and geocoding resolves to None — final chart must
    carry a machine-readable status so the LLM prompt and the API response
    can both surface it (no silent loss)."""
    profile = _Profile(birth_latitude=None, birth_longitude=None)
    monkeypatch.setenv("GEOCODING_ENABLED", "1")
    monkeypatch.setattr(astrology, "resolve_coordinates", lambda *a, **k: None)
    chart = astrology.astrology_context(profile)
    assert chart.get("status") == "calculated"  # planets still calculated
    assert chart.get("geocoding_status") == "unresolved"
    assert chart.get("houses_status") == "coordinates_missing"


def test_astrology_context_geocoding_success_marks_resolved(monkeypatch):
    profile = _Profile(birth_latitude=None, birth_longitude=None)
    monkeypatch.setenv("GEOCODING_ENABLED", "1")
    monkeypatch.setattr(
        astrology, "resolve_coordinates", lambda *a, **k: (-23.55, -46.63)
    )
    chart = astrology.astrology_context(profile)
    assert chart.get("geocoding_status") == "resolved"
    assert chart.get("geocoding_source") == "nominatim"


# ---------------------------------------------------------------------------
# Bug 4: blocking external call in astrology path
# ---------------------------------------------------------------------------

def test_astrology_context_skips_geocoding_when_disabled(monkeypatch):
    """resolve_coordinates must short-circuit when GEOCODING_ENABLED != 1.
    The previous code read the env at call time; the test ensures the
    resolved flag is exposed in the chart for downstream observability."""
    profile = _Profile(birth_latitude=None, birth_longitude=None)
    monkeypatch.setenv("GEOCODING_ENABLED", "0")
    called = {"n": 0}

    def fake_resolve(*a, **k):
        called["n"] += 1
        return None

    monkeypatch.setattr(astrology, "resolve_coordinates", fake_resolve)
    chart = astrology.astrology_context(profile)
    # The geocoding-disabled path must NOT call the resolver at all
    assert called["n"] == 0
    assert chart.get("geocoding_status") == "disabled"


# ---------------------------------------------------------------------------
# Bug 5: token-truncation risk for premium content
# ---------------------------------------------------------------------------

def test_minimax_max_tokens_scales_with_content_length():
    """Content rules demand up to 14 paragraphs (~2000+ words). 2400 tokens
    can truncate mid-paragraph. The engine must request a larger ceiling for
    long premium reading types and a sufficient one for short ones."""
    long_types = [
        "site:content:mapa_astral_completo",  # 10-14 paragraphs
        "site:content:mapa_do_amor_sinastria",  # 9-12
        "site:content:mapa_da_carreira",  # 8-11
        "site:content:mapa_da_prosperidade",  # 8-11
    ]
    for cid in long_types:
        assert engine._max_tokens_for(cid) >= 3200, f"{cid} budget too small"


def test_horoscopo_diario_budget_acomoda_raciocinio_m2x():
    """horoscopo_diario precisa de >= 2500 tokens porque M2.x é modelo de
    raciocínio: <think>…</think> CONTA contra max_tokens e pode consumir o
    budget inteiro antes de produzir saída. Com 1500, o modelo esgotava em
    raciocínio e devolvia corpo vazio (finish_reason=length), caindo no
    fallback editorial. Nunca reduzir abaixo de 2000 sem medir consumo real."""
    budget = engine._max_tokens_for("site:content:horoscopo_diario")
    assert budget >= 2000, f"budget {budget} pequeno demais — M2.x queima tokens em raciocínio"


# ---------------------------------------------------------------------------
# Bug 6: fallback is presented as if it were a paid reading
# ---------------------------------------------------------------------------

def test_generate_reading_indicates_fallback_when_no_api_key(monkeypatch):
    """When the MiniMax path is unavailable, generate_reading must return
    a ReadingResult whose source='fallback' so the caller can record the
    fallback and the buyer sees the truth — not a generic text branded as
    a personalized premium reading."""
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    result = engine.generate_reading(
        "site:content:horoscopo_diario", "Horóscopo diário", _Profile(), "pt-BR", "Alex"
    )
    assert isinstance(result, engine.ReadingResult)
    assert result.source == "fallback"
    assert "<p>" in result.body_html
    assert result.warning  # non-empty warning so the API can surface it


def test_generate_reading_returns_source_llm_when_live(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    monkeypatch.setattr(engine, "_call_minimax", lambda prompt, locale="pt-BR", **kwargs: "Parágrafo um.\n\nParágrafo dois.")
    result = engine.generate_reading(
        "site:content:horoscopo_diario", "Horóscopo diário", _Profile(), "pt-BR", "Alex"
    )
    assert isinstance(result, engine.ReadingResult)
    assert result.source == "minimax"
    assert "<p>" in result.body_html


def test_generate_reading_fallback_when_live_errors(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")

    def boom(_prompt, _locale="pt-BR", **kwargs):
        raise RuntimeError("MiniMax indisponível: HTTPError")

    monkeypatch.setattr(engine, "_call_minimax", boom)
    result = engine.generate_reading(
        "site:content:horoscopo_diario", "Horóscopo diário", _Profile(), "pt-BR", "Alex"
    )
    assert isinstance(result, engine.ReadingResult)
    assert result.source == "fallback"
    assert "<p>" in result.body_html


# ---------------------------------------------------------------------------
# Sanity: house_for boundary in synthetic wrap-around cusps
# ---------------------------------------------------------------------------

def test_house_for_synthetic_wraparound_cusp12_belongs_to_house12():
    """When cusps[11] > cusps[0] (e.g. polar Placidus), a planet at exactly
    cusps[11] must be classified as house 12 (start of house 12), not house 1."""
    # synthetic: cusp 12 = 350°, cusp 1 = 14° (wrap of 24°)
    cusps = (14.0, 50, 80, 110, 140, 170, 200, 230, 260, 290, 320, 350.0)
    assert astrology._house_for(350.0, cusps) == 12


def test_house_for_planet_inside_house_12_wraparound():
    # Convention: cusps[i] = start of house i+1. With these synthetic cusps,
    # house 1 = [14, 50) and house 12 = [350, 14) wraps across 0°.
    cusps = (14.0, 50, 80, 110, 140, 170, 200, 230, 260, 290, 320, 350.0)
    # 340° is in house 11 [320, 350)
    assert astrology._house_for(340.0, cusps) == 11
    # 5° is in house 12 [350, 14) — the wrap house
    assert astrology._house_for(5.0, cusps) == 12
    # 13.99° is still in house 12 (just before cusp 12 ends)
    assert astrology._house_for(13.99, cusps) == 12
    # 14° is cusp 1 start → house 1
    assert astrology._house_for(14.0, cusps) == 1
