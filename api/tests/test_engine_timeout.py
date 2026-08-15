"""Tratamento de TimeoutError na geração seção-a-seção.

Timeouts consecutivos são evidência de API indisponível — diferente de erros de
conteúdo (idioma/truncamento) que valem retry. Após N timeouts seguidos,
_attempt_with_model deve desistir do modelo sem esgotar todas as tentativas,
economizando (max_attempts - N) × timeout_seconds de espera por seção.

Medição de 13/08/2026: 5 timeouts em 27 chamadas (18,5%), latência mediana 22s,
cauda 40s. Com timeout=60s e 5 tentativas, uma seção com API down custava 300s.
Com timeout=40s e abandono após 2 consecutivos: máximo 2 × 40s = 80s.
"""

from __future__ import annotations

from datetime import date

import pytest

from app import engine


class _Profile:
    def __init__(self):
        self.birth_date = date(1990, 3, 15)
        self.birth_time = None
        self.birth_city = "São Paulo"
        self.birth_country = "BR"
        self.birth_timezone = "America/Sao_Paulo"
        self.birth_latitude = None
        self.birth_longitude = None
        self.partner_name = ""
        self.partner_birth_date = None
        self.partner_birth_time = None
        self.partner_birth_city = ""


_SECTION_CONTENT_ID = "site:content:horoscopo_diario"
_GOOD_SECTION_MD = "## Identificação\n### O dia reflete você\n\nUm parágrafo bem formado que termina com ponto final adequado."


def _timeout_error():
    raise RuntimeError("MiniMax indisponível: TimeoutError")


def _content_error():
    raise RuntimeError("MiniMax indisponível: URLError")


def test_timeout_desiste_apos_n_consecutivos(monkeypatch):
    """Com limit=2, após 2 TimeoutErrors seguidos o modelo é abandonado sem esgotar 5 tentativas."""
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    monkeypatch.setenv("MINIMAX_SECTION_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("MINIMAX_SECTION_TIMEOUT_CONSECUTIVE_LIMIT", "2")
    monkeypatch.setenv("MINIMAX_SECTION_TIMEOUT_SECONDS", "1")  # torna o teste rápido

    call_count = [0]

    def _fake_call(prompt, locale="pt-BR", **kwargs):
        call_count[0] += 1
        _timeout_error()

    monkeypatch.setattr(engine, "_call_minimax", _fake_call)

    profile = _Profile()
    context = engine._profile_context(profile, "")
    sibling_titles = ["Identificação", "Relações e Trabalho", "Direção Prática"]

    section, fell_back = engine._generate_section(
        _SECTION_CONTENT_ID, "Horóscopo Diário",
        "Identificação", "O dia reflete você",
        1, 3, sibling_titles, context, "pt-BR", profile,
    )

    # Deve ter tentado exatamente 2 vezes (o limite), não 5
    assert call_count[0] == 2, (
        f"Esperava 2 tentativas (limit=2), mas fez {call_count[0]}"
    )
    # Seção vem do fallback editorial
    assert fell_back is True


def test_timeout_isolado_nao_abandona_cedo(monkeypatch):
    """1 timeout seguido de sucesso: não abandona — deve tentar até passar."""
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    monkeypatch.setenv("MINIMAX_SECTION_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("MINIMAX_SECTION_TIMEOUT_CONSECUTIVE_LIMIT", "2")
    monkeypatch.setenv("MINIMAX_SECTION_TIMEOUT_SECONDS", "1")

    respostas = [RuntimeError("MiniMax indisponível: TimeoutError"), _GOOD_SECTION_MD]
    call_count = [0]

    def _fake_call(prompt, locale="pt-BR", **kwargs):
        r = respostas[call_count[0]]
        call_count[0] += 1
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(engine, "_call_minimax", _fake_call)

    profile = _Profile()
    context = engine._profile_context(profile, "")
    sibling_titles = ["Identificação", "Relações e Trabalho", "Direção Prática"]

    section, fell_back = engine._generate_section(
        _SECTION_CONTENT_ID, "Horóscopo Diário",
        "Identificação", "O dia reflete você",
        1, 3, sibling_titles, context, "pt-BR", profile,
    )

    assert call_count[0] == 2, "1 timeout + 1 sucesso = 2 chamadas"
    assert fell_back is False
    assert section["content"]


def test_erros_de_conteudo_nao_acionam_abandono_de_timeout(monkeypatch):
    """URLError/conteúdo ruim NÃO contam como timeout — devem esgotar tentativas normalmente."""
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    monkeypatch.setenv("MINIMAX_SECTION_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("MINIMAX_SECTION_TIMEOUT_CONSECUTIVE_LIMIT", "2")
    monkeypatch.setenv("MINIMAX_SECTION_TIMEOUT_SECONDS", "1")

    call_count = [0]

    def _fake_call(prompt, locale="pt-BR", **kwargs):
        call_count[0] += 1
        raise RuntimeError("MiniMax indisponível: URLError")  # não é TimeoutError

    monkeypatch.setattr(engine, "_call_minimax", _fake_call)

    profile = _Profile()
    context = engine._profile_context(profile, "")
    sibling_titles = ["Identificação", "Relações e Trabalho", "Direção Prática"]

    section, fell_back = engine._generate_section(
        _SECTION_CONTENT_ID, "Horóscopo Diário",
        "Identificação", "O dia reflete você",
        1, 3, sibling_titles, context, "pt-BR", profile,
    )

    # 3 tentativas, nenhum abandono antecipado
    assert call_count[0] == 3, (
        f"Esperava esgotar as 3 tentativas normais, mas fez {call_count[0]}"
    )
    assert fell_back is True


def test_timeout_intercalado_reseta_contador(monkeypatch):
    """Timeout → sucesso → timeout: o 2º timeout não conta como '2 consecutivos'."""
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    monkeypatch.setenv("MINIMAX_SECTION_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("MINIMAX_SECTION_TIMEOUT_CONSECUTIVE_LIMIT", "2")
    monkeypatch.setenv("MINIMAX_SECTION_TIMEOUT_SECONDS", "1")

    # T=timeout, G=good
    responses: list = [
        RuntimeError("MiniMax indisponível: TimeoutError"),  # timeout → consecutivos=1
        _GOOD_SECTION_MD,  # sucesso → consecutivos=0; mas conteúdo ok, retorna
    ]
    call_count = [0]

    def _fake_call(prompt, locale="pt-BR", **kwargs):
        r = responses[call_count[0]]
        call_count[0] += 1
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(engine, "_call_minimax", _fake_call)

    profile = _Profile()
    context = engine._profile_context(profile, "")
    sibling_titles = ["Identificação", "Relações e Trabalho", "Direção Prática"]

    section, fell_back = engine._generate_section(
        _SECTION_CONTENT_ID, "Horóscopo Diário",
        "Identificação", "O dia reflete você",
        1, 3, sibling_titles, context, "pt-BR", profile,
    )

    assert call_count[0] == 2
    assert fell_back is False
    assert section["content"]


def test_timeout_padrao_e_40_segundos():
    """O default do timeout de seção é 40s (mediana 22s, cauda 40s — sem perder chamadas válidas)."""
    assert engine._SECTION_TIMEOUT_SECONDS_DEFAULT == "40"


def test_limite_consecutivo_padrao_e_2():
    """O default do limite de timeouts consecutivos é 2."""
    assert engine._SECTION_TIMEOUT_CONSECUTIVE_LIMIT_DEFAULT == "2"
