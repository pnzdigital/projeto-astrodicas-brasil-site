"""Hora assumida no fluxo pago: o cliente não pode pagar e receber Ascendente
vazio nem uma leitura que ignora o Ascendente em silêncio.

Mesma decisão já aplicada na prévia grátis (commit 913fcd8): quando a hora de
nascimento não vem, o backend assume 00:00, calcula o Ascendente normalmente e
marca ``birth_time_assumed``. Aqui a diferença é onde o fluxo rende:

- ``astrology.astrology_context`` já alimenta o prompt da leitura paga via
  ``engine._profile_context``. Se o Ascendente for silenciosamente descartado,
  o MiniMax recebe um chart sem Ascendente e omite o tema — o cliente pagou
  pra receber algo incompleto.
- A leitura devolvida e o snapshot persistido precisam carregar o flag para
  a UI poder re-exibir o aviso ao lado do Ascendente calculado, do mesmo jeito
  que a prévia grátis já faz.
"""

from __future__ import annotations

from datetime import date

import pytest

from app import astrology, engine


class _Profile:
    """Replica mínima do model Profile para testes de unit."""

    def __init__(self, *, birth_date, birth_time, birth_city="Recife", birth_country="BR",
                 birth_timezone="America/Recife", birth_latitude="-8.0476", birth_longitude="-34.877"):
        self.birth_date = birth_date
        self.birth_time = birth_time
        self.birth_city = birth_city
        self.birth_country = birth_country
        self.birth_timezone = birth_timezone
        self.birth_latitude = birth_latitude
        self.birth_longitude = birth_longitude
        self.partner_name = ""
        self.partner_birth_date = None
        self.partner_birth_time = None
        self.partner_birth_city = ""
        self.partner_country = "BR"
        self.partner_birth_timezone = ""


@pytest.fixture()
def recife_coords(monkeypatch):
    """Geocoding é desligado no conftest; fixamos coordenadas reais de Recife."""
    monkeypatch.setattr(
        astrology,
        "resolve_coordinates",
        lambda city, country: (-8.0476, -34.877),
    )


def test_astrology_context_calculates_ascendant_when_birth_time_missing(recife_coords):
    """O bug comercial: cliente paga o mapa astral completo sem hora e o
    backend devolve um chart sem Ascendente, sem nenhuma marcação de incerteza.
    A prévia grátis (commit 913fcd8) já assume 00:00 e devolve o Ascendente;
    o fluxo pago tem que fazer o mesmo para que a leitura completa não saia
    silenciosamente sem falar de Ascendente."""
    profile = _Profile(birth_date=date(1990, 5, 20), birth_time=None)

    chart = astrology.astrology_context(profile)

    assert chart.get("status") == "calculated"
    assert chart.get("birth_time_assumed") is True
    # Ascendente tem que estar presente — o ponto mais sensível à hora do mapa
    # inteiro, mas existe e pode ser declarado como estimativa.
    assert "ascendant" in chart, "Ascendente precisa ser calculado (assumindo 00:00) quando a hora falta"
    assert chart["ascendant"]["sign"] in astrology.SIGNS
    # Casas dependem de hora; flag explícita do motivo, não inferência silenciosa.
    assert chart.get("houses_status") == "calculated_with_assumed_midnight"


def test_astrology_context_with_real_birth_time_has_no_assumed_flag(recife_coords):
    """Com hora real, nada de flag fake — o cliente não merece ler aviso de
    estimativa quando deu o dado completo."""
    from datetime import time

    profile = _Profile(birth_date=date(1990, 5, 20), birth_time=time(14, 30))

    chart = astrology.astrology_context(profile)

    assert chart.get("birth_time_assumed") is False
    assert "ascendant" in chart
    assert "houses" in chart
    assert chart.get("houses_status") != "calculated_with_assumed_midnight"


def test_profile_context_propagates_birth_time_assumed_to_prompt():
    """O prompt enviado ao LLM precisa saber que o Ascendente é estimado,
    senão o texto pago afirma um Ascendente com certeza que não tem. Mesma
    política do commit 913fcd8 para a prévia grátis."""
    profile = _Profile(birth_date=date(1990, 5, 20), birth_time=None)
    captured = {}

    class _Capture:
        def __init__(self, inner): self.inner = inner
        def __getattr__(self, name): return getattr(self.inner, name)

    real_call = engine._call_minimax

    def spy(prompt):
        captured["prompt"] = prompt
        # Devolve texto curto sem chamar a rede: o teste só inspeciona o prompt.
        return "<p>Texto curto de teste.</p>"

    import os
    old_key = os.environ.pop("MINIMAX_API_KEY", None)
    try:
        # Sem chave o engine cai no fallback e nunca monta o prompt. Forçamos
        # a chave para exercitar o caminho do _prompt que é onde mora o aviso.
        os.environ["MINIMAX_API_KEY"] = "test-key"
        engine._call_minimax = staticmethod(spy)  # type: ignore[assignment]
        engine.generate_reading(
            "site:content:mapa_astral_completo",
            "Mapa Astral Completo",
            profile,
            "pt-BR",
            "Cliente Teste",
        )
    finally:
        engine._call_minimax = real_call  # type: ignore[assignment]
        os.environ.pop("MINIMAX_API_KEY", None)
        if old_key is not None:
            os.environ["MINIMAX_API_KEY"] = old_key

    prompt = captured["prompt"]
    # Sinal explícito pro LLM: o Ascendente é estimado, não afirma com certeza.
    assert "birth_time_assumed" in prompt, (
        "Prompt precisa declarar para o LLM que a hora foi assumida; sem isso "
        "o texto pago afirma o Ascendente com certeza que não tem."
    )
    # Texto localizado explica a incerteza (independe do idioma; o aviso está
    # em qualquer idioma presente).
    assert (
        "Ascendente" in prompt and ("estimad" in prompt.lower() or "estim" in prompt.lower())
    ), "Prompt precisa carregar o aviso de Ascendente estimado no idioma correto"


def test_generate_reading_returns_reading_with_assumed_flag(monkeypatch):
    """O resultado devolvido ao chamador (a rota paga) precisa carregar
    ``birth_time_assumed`` para a UI poder re-exibir o aviso ao lado do
    Ascendente calculado. Hoje o contrato só devolve body_html/source/warning,
    então a UI não tem como saber — bug comercial, não só técnico."""
    profile = _Profile(birth_date=date(1990, 5, 20), birth_time=None)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)

    result = engine.generate_reading(
        "site:content:mapa_astral_completo",
        "Mapa Astral Completo",
        profile,
        "pt-BR",
    )

    assert getattr(result, "birth_time_assumed", None) is True, (
        "ReadingResult precisa carregar birth_time_assumed para a UI renderizar o aviso no fluxo pago"
    )


def test_generate_reading_es_ar_localized_assumed_warning(monkeypatch):
    """Aviso vem no idioma do cliente — pt-BR e es-AR têm textos diferentes."""
    profile = _Profile(birth_date=date(1990, 5, 20), birth_time=None)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)

    result_br = engine.generate_reading(
        "site:content:mapa_astral_completo",
        "Mapa Astral Completo",
        profile,
        "pt-BR",
    )
    result_es = engine.generate_reading(
        "site:content:mapa_astral_completo",
        "Mapa Astral Completo",
        profile,
        "es-AR",
    )

    warning_attr = "ascendant_warning"
    assert hasattr(result_br, warning_attr), (
        "ReadingResult precisa carregar ascendant_warning localizado para o cliente ver junto do Ascendente"
    )
    assert "Ascendente" in result_br.ascendant_warning["pt-BR"]
    assert "Ascendente" in result_es.ascendant_warning["es-AR"]
    assert result_br.ascendant_warning["pt-BR"] != result_es.ascendant_warning["es-AR"]
