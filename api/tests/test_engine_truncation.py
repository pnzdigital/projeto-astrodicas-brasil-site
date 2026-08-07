"""Nenhuma leitura seccionada sai truncada ou curta demais para o cliente pagante.

Medição real de produção (2026-08-06, 3 mapas via MiniMax):
  - mapa_astral: 946 palavras em 1 parágrafo único (formato antigo, sem seção;
    resolvido pela Etapa A do seccionamento).
  - mapa_carreira: 638 palavras em 1 parágrafo único, TRUNCADO no meio da
    frase: "...transformando-a em motivação para".
  - mapa_prosperidade: 172 palavras (caiu em fallback editorial).

Este arquivo trava as mesmas propriedades de antes (seções presentes, palavras
mínimas, final gramaticalmente completo) mas agora sobre a geração
SEÇÃO-A-SEÇÃO (``_generate_section`` / ``_generate_reading_sections``): cada
chamada ao MiniMax pede UMA seção só, e a reprovação (idioma ou truncamento)
regenera SÓ aquela seção — não o documento inteiro.
"""

import re
from datetime import date

from app import engine


class _Profile:
    def __init__(self, birth_date=None, birth_city="São Paulo"):
        self.birth_date = birth_date
        self.birth_time = None
        self.birth_city = birth_city
        self.birth_country = "BR"
        self.birth_timezone = "America/Sao_Paulo"
        self.birth_latitude = None
        self.birth_longitude = None
        self.partner_name = ""
        self.partner_birth_date = None
        self.partner_birth_time = None
        self.partner_birth_city = ""


def _profile():
    return _Profile(date(1990, 3, 15))


def _section_markdown(title: str, subtitle: str, *, complete: bool = True) -> str:
    """Markdown de UMA seção só, no formato que ``_generate_section`` espera
    (mesmo com ``_parse_markdown_sections`` rodando sobre um bloco único)."""
    body = (
        f"Primeiro parágrafo sobre {title} considerando o mapa calculado da pessoa. "
        "Um trígono de água conecta a Lua ao Ascendente em Peixes, trazendo sensibilidade.\n\n"
        f"Segundo parágrafo aprofunda {title} com base nas posições reais fornecidas."
    )
    if complete:
        body += " Esse movimento fecha o tema com uma orientação prática e concreta."
    else:
        # Corta a frase no meio, sem pontuação final — mesmo defeito
        # observado em produção no mapa_carreira.
        body += " Esse movimento vem transformando-a em motivação para"
    return f"## {title}\n### {subtitle}\n{body}"


_TITLE_RE = re.compile(r"Título desta seção: (.+)")


def _section_title_from_prompt(prompt: str) -> str:
    match = _TITLE_RE.search(prompt)
    return match.group(1).strip() if match else ""


def test_secoes_por_content_id_astral_e_carreira_definidas():
    astral = engine.sections_for("site:content:mapa_astral_completo")
    carreira = engine.sections_for("site:content:mapa_da_carreira")
    assert len(astral) == 15
    assert len(carreira) == 14


def test_looks_truncated_detecta_frase_cortada():
    assert engine._looks_truncated("...transformando-a em motivação para")
    assert engine._looks_truncated("uma frase sem pontuação no final")
    assert not engine._looks_truncated("Uma frase completa, com final correto.")
    assert not engine._looks_truncated('Termina com aspas de fechamento.”')
    assert engine._looks_truncated("")


def test_refaz_apenas_a_secao_truncada_sem_tocar_nas_outras(monkeypatch):
    """A propriedade central do seccionamento: uma seção reprovada (truncada)
    é refeita sozinha — as outras 13 seções não são regeneradas."""
    monkeypatch.setenv("MINIMAX_API_KEY", "chave-de-teste")
    monkeypatch.setenv("MINIMAX_SECTION_MAX_ATTEMPTS", "3")
    content_id = "site:content:mapa_da_carreira"
    expected = engine.sections_for(content_id)
    target_title, _target_subtitle = expected[-1]
    calls_by_title: dict[str, int] = {}

    def _fake(prompt, locale="pt-BR", max_tokens=None, timeout=None):
        title = _section_title_from_prompt(prompt)
        calls_by_title[title] = calls_by_title.get(title, 0) + 1
        subtitle = next(s for t, s in expected if t == title)
        if title == target_title and calls_by_title[title] < 3:
            return _section_markdown(title, subtitle, complete=False)
        return _section_markdown(title, subtitle, complete=True)

    monkeypatch.setattr(engine, "_call_minimax", _fake)
    result = engine.generate_reading(content_id, "Mapa da Carreira", _profile())

    assert result.source == "minimax"
    assert len(result.sections) == len(expected)
    assert calls_by_title[target_title] == 3, "seção truncada deveria ter sido refeita até sair completa"
    for title, _subtitle in expected:
        if title != target_title:
            assert calls_by_title[title] == 1, f"seção '{title}' não deveria ter sido regenerada"
    assert not engine._looks_truncated(result.sections[-1]["content"])


def test_secao_cai_no_fallback_pontual_sem_derrubar_as_outras(monkeypatch):
    """Quando UMA seção esgota as tentativas, só ela vira fallback — as
    demais seções continuam com o texto gerado pelo MiniMax."""
    monkeypatch.setenv("MINIMAX_API_KEY", "chave-de-teste")
    monkeypatch.setenv("MINIMAX_SECTION_MAX_ATTEMPTS", "2")
    content_id = "site:content:mapa_da_carreira"
    expected = engine.sections_for(content_id)
    target_title, _target_subtitle = expected[0]
    calls_by_title: dict[str, int] = {}

    def _fake(prompt, locale="pt-BR", max_tokens=None, timeout=None):
        title = _section_title_from_prompt(prompt)
        calls_by_title[title] = calls_by_title.get(title, 0) + 1
        subtitle = next(s for t, s in expected if t == title)
        if title == target_title:
            return _section_markdown(title, subtitle, complete=False)
        return _section_markdown(title, subtitle, complete=True)

    monkeypatch.setattr(engine, "_call_minimax", _fake)
    result = engine.generate_reading(content_id, "Mapa da Carreira", _profile())

    assert calls_by_title[target_title] == 2, "deveria ter esgotado as 2 tentativas na seção que sempre trunca"
    assert result.source == "fallback", "leitura com 1 seção em fallback precisa continuar identificável como fallback"
    assert len(result.sections) == len(expected)
    fallback_section = next(s for s in result.sections if s["order"] == 1)
    other_section = next(s for s in result.sections if s["order"] != 1)
    # A seção em fallback usa o template editorial (não o texto "Primeiro
    # parágrafo sobre ..." que o mock devolve para o MiniMax); as demais
    # seções mantêm o texto gerado normalmente.
    assert "Primeiro parágrafo sobre" not in fallback_section["content"]
    assert "Primeiro parágrafo sobre" in other_section["content"]


def test_leitura_seccionada_bem_sucedida_trava_tres_propriedades(monkeypatch):
    """A propriedade central pedida: seções mínimas, palavras mínimas e final
    completo, para mapa_astral_completo e mapa_da_carreira."""
    monkeypatch.setenv("MINIMAX_API_KEY", "chave-de-teste")
    for content_id, min_words in (
        ("site:content:mapa_astral_completo", 600),
        ("site:content:mapa_da_carreira", 550),
    ):
        expected = engine.sections_for(content_id)

        def _fake(prompt, locale="pt-BR", max_tokens=None, timeout=None, _expected=expected):
            title = _section_title_from_prompt(prompt)
            subtitle = next(s for t, s in _expected if t == title)
            return _section_markdown(title, subtitle, complete=True)

        monkeypatch.setattr(engine, "_call_minimax", _fake)
        result = engine.generate_reading(content_id, "Leitura", _profile())

        assert result.source == "minimax"
        # (1) número mínimo de seções presentes
        assert len(result.sections) == len(expected)

        # (2) contagem mínima de palavras (soma de todas as seções)
        total_words = sum(len((s.get("content") or "").split()) for s in result.sections)
        assert total_words >= min_words, f"{content_id}: {total_words} palavras, esperava >= {min_words}"

        # (3) termina de forma completa — nunca no meio de frase/palavra
        assert not engine._looks_truncated(result.sections[-1]["content"])
        assert result.body_html.rstrip().endswith("</p>")


def test_todas_as_secoes_falham_cai_no_fallback_completo(monkeypatch):
    """Se o MiniMax falhar em toda seção (ex.: chave revogada em produção), o
    resultado ainda é uma leitura completa e honestamente marcada como
    fallback — nunca um documento pela metade."""
    monkeypatch.setenv("MINIMAX_API_KEY", "chave-de-teste")
    monkeypatch.setenv("MINIMAX_SECTION_MAX_ATTEMPTS", "2")
    content_id = "site:content:mapa_astral_completo"

    def _fake(prompt, locale="pt-BR", max_tokens=None, timeout=None):
        raise RuntimeError("MiniMax indisponível: URLError")

    monkeypatch.setattr(engine, "_call_minimax", _fake)
    result = engine.generate_reading(content_id, "Mapa Astral Completo", _profile())

    assert result.source == "fallback"
    assert len(result.sections) == len(engine.sections_for(content_id))
    assert not engine._looks_truncated(result.sections[-1]["content"])
