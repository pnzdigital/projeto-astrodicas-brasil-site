"""Nenhuma leitura seccionada sai truncada ou curta demais para o cliente pagante.

Medição real de produção (2026-08-06, 3 mapas via MiniMax):
  - mapa_astral: 946 palavras em 1 parágrafo único (formato antigo, sem seção;
    resolvido pela Etapa A do seccionamento).
  - mapa_carreira: 638 palavras em 1 parágrafo único, TRUNCADO no meio da
    frase: "...transformando-a em motivação para".
  - mapa_prosperidade: 172 palavras (caiu em fallback editorial).

Este arquivo trava três propriedades para os content_ids seccionados
(mapa_astral_completo e mapa_da_carreira): (1) número mínimo de seções
presentes, (2) contagem mínima de palavras, (3) a resposta termina de forma
gramaticalmente completa (nunca no meio de uma frase/palavra) — e que uma
resposta que viola qualquer uma dessas é REPROVADA e regenerada pelo mesmo
laço de tentativas do guard de idioma, só caindo no fallback depois de
esgotar as tentativas.
"""

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


def _make_markdown(content_id: str, *, complete: bool = True, skip_last_n: int = 0) -> str:
    """Monta um markdown seccionado válido a partir de SECTIONS_BY_CONTENT_ID.

    ``skip_last_n`` omite as N últimas seções (simula resposta com poucas
    seções). ``complete=False`` corta a última frase da última seção sem
    pontuação final (simula truncamento por max_tokens).
    """
    expected = engine.sections_for(content_id)
    keep = expected[: len(expected) - skip_last_n] if skip_last_n else expected
    parts = []
    for i, (title, subtitle) in enumerate(keep):
        is_last = i == len(keep) - 1
        body = (
            f"Primeiro parágrafo sobre {title} considerando o mapa calculado da pessoa. "
            "Um trígono de água conecta a Lua ao Ascendente em Peixes, trazendo sensibilidade.\n\n"
            f"Segundo parágrafo aprofunda {title} com base nas posições reais fornecidas."
        )
        if is_last and not complete:
            # Corta a frase no meio, sem pontuação final — mesmo defeito
            # observado em produção no mapa_carreira.
            body += " Esse movimento vem transformando-a em motivação para"
        else:
            body += " Esse movimento fecha o tema com uma orientação prática e concreta."
        parts.append(f"## {title}\n### {subtitle}\n{body}")
    return "\n\n".join(parts)


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


def test_refaz_quando_a_ultima_secao_vem_truncada(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "chave-de-teste")
    monkeypatch.setenv("MINIMAX_MAX_ATTEMPTS", "3")
    content_id = "site:content:mapa_da_carreira"
    respostas = [
        _make_markdown(content_id, complete=False),  # truncado — reprova
        _make_markdown(content_id, complete=False),  # truncado de novo — reprova
        _make_markdown(content_id, complete=True),   # completo — aceita
    ]
    chamadas = []

    def _fake(prompt, locale="pt-BR"):
        chamadas.append(locale)
        return respostas[len(chamadas) - 1]

    monkeypatch.setattr(engine, "_call_minimax", _fake)
    result = engine.generate_reading(content_id, "Mapa da Carreira", _profile())

    assert len(chamadas) == 3, "deveria ter refeito a chamada até a última seção sair completa"
    assert result.source == "minimax"
    assert not engine._looks_truncated(result.sections[-1]["content"])


def test_cai_no_fallback_quando_todas_as_tentativas_vem_truncadas(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "chave-de-teste")
    monkeypatch.setenv("MINIMAX_MAX_ATTEMPTS", "2")
    content_id = "site:content:mapa_astral_completo"
    chamadas = []

    def _fake(prompt, locale="pt-BR"):
        chamadas.append(locale)
        return _make_markdown(content_id, complete=False)

    monkeypatch.setattr(engine, "_call_minimax", _fake)
    result = engine.generate_reading(content_id, "Mapa Astral Completo", _profile())

    assert len(chamadas) == 2
    assert result.source == "fallback"
    # O fallback seccionado continua identificável e com todas as seções
    # esperadas, mesmo tendo desistido do LLM.
    assert len(result.sections) == len(engine.sections_for(content_id))
    assert not engine._looks_truncated(result.sections[-1]["content"])


def test_refaz_quando_vem_poucas_secoes(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "chave-de-teste")
    monkeypatch.setenv("MINIMAX_MAX_ATTEMPTS", "3")
    content_id = "site:content:mapa_astral_completo"
    respostas = [
        _make_markdown(content_id, skip_last_n=6),  # só 9 de 15 — reprova
        _make_markdown(content_id, skip_last_n=6),  # de novo — reprova
        _make_markdown(content_id),                 # 15 completas — aceita
    ]
    chamadas = []

    def _fake(prompt, locale="pt-BR"):
        chamadas.append(locale)
        return respostas[len(chamadas) - 1]

    monkeypatch.setattr(engine, "_call_minimax", _fake)
    result = engine.generate_reading(content_id, "Mapa Astral Completo", _profile())

    assert len(chamadas) == 3
    assert result.source == "minimax"
    assert len(result.sections) == len(engine.sections_for(content_id))


def test_leitura_seccionada_bem_sucedida_trava_tres_propriedades(monkeypatch):
    """A propriedade central pedida: seções mínimas, palavras mínimas e final
    completo, para mapa_astral_completo e mapa_da_carreira."""
    monkeypatch.setenv("MINIMAX_API_KEY", "chave-de-teste")
    for content_id, min_words in (
        ("site:content:mapa_astral_completo", 600),
        ("site:content:mapa_da_carreira", 550),
    ):
        expected = engine.sections_for(content_id)

        def _fake(prompt, locale="pt-BR", _cid=content_id):
            return _make_markdown(_cid, complete=True)

        monkeypatch.setattr(engine, "_call_minimax", _fake)
        result = engine.generate_reading(content_id, "Leitura", _profile())

        assert result.source == "minimax"
        # (1) número mínimo de seções presentes
        assert len(result.sections) >= len(expected) - 1

        # (2) contagem mínima de palavras (soma de todas as seções)
        total_words = sum(len((s.get("content") or "").split()) for s in result.sections)
        assert total_words >= min_words, f"{content_id}: {total_words} palavras, esperava >= {min_words}"

        # (3) termina de forma completa — nunca no meio de frase/palavra
        assert not engine._looks_truncated(result.sections[-1]["content"])
        assert result.body_html.rstrip().endswith("</p>")
