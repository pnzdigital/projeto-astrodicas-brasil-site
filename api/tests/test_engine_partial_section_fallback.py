"""Bug real de produção (2026-08-17, reading 5a769308-a72b-4657-a4f2-7ebe13bdc227,
locale es-AR, site:mapa_astral): as 15 seções vieram do modelo com sucesso,
mas a leitura inteira foi jogada fora e o job esgotou 3 tentativas — cliente
pagou e não recebeu nada.

Causa raiz (achada em investigação anterior no mesmo dia, checkpoint
17e6505c-97dc-4091-9312-e7e8c1b07116): quando UMA seção esgota as tentativas
e cai no template editorial local (``_fallback_section``), o OR binário
``fell_back_any`` marca a leitura INTEIRA como ``source="fallback"``. O
fail_closed em main.py então recusa persistir e joga fora as outras 14
seções boas junto — mesmo quando só uma seção precisava de mais uma chance.

Este teste força exatamente 1 seção em 15 a esgotar as tentativas do
primeiro laço, mas sair limpa numa geração extra isolada logo depois — e
prova que a leitura deve sair como "minimax" (não descartada), sem
enfraquecer o fail_closed: se a seção HONESTAMENTE não conseguir texto
limpo mesmo com a chance extra, a leitura continua saindo "fallback".
"""

from datetime import date

import pytest

from app import engine

CONTENT_ID = "site:content:mapa_astral_completo"
LOCALE = "es-AR"

LIMPIO = (
    "Un párrafo limpio y correcto en español rioplatense sobre esta sección de la carta, "
    "sin ningún error de idioma para esta prueba.\n\n"
    "Segundo párrafo que cierra el tema con una orientación práctica y concreta para hoy."
)
CONTAMINADO = "Esse posicionamento sugere que成长 pessoal acontece devagar."


class _Profile:
    def __init__(self):
        self.birth_date = date(1990, 3, 15)
        self.birth_time = None
        self.birth_city = "Buenos Aires"
        self.birth_country = "AR"
        self.birth_timezone = "America/Argentina/Buenos_Aires"
        self.birth_latitude = None
        self.birth_longitude = None
        self.partner_name = ""
        self.partner_birth_date = None
        self.partner_birth_time = None
        self.partner_birth_city = ""


def _section_markdown(title: str, subtitle: str, body: str) -> str:
    return f"## {title}\n### {subtitle}\n{body}"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "chave-de-teste")
    monkeypatch.setenv("MINIMAX_SECTION_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("MINIMAX_SECTION_POOL_SIZE", "4")
    monkeypatch.setenv("MINIMAX_MODEL_LONG", "")


def _expected():
    return engine.sections_for(CONTENT_ID, None, LOCALE)


def test_uma_secao_com_dificuldade_nao_pode_jogar_fora_as_outras_catorze(monkeypatch):
    """RED antes do fix: as 14 seções boas + a 15ª (que sai limpa numa
    tentativa extra) deveriam render source='minimax'. Hoje o OR binário
    marca a leitura inteira como fallback e o fail_closed descarta tudo."""
    expected = _expected()
    alvo_titulo = expected[7][0]
    chamadas_por_secao: dict[str, int] = {}

    def _fake(prompt, locale="pt-BR", max_tokens=None, timeout=None, model=None, section_label=""):
        subtitle = next(s for t, s in expected if t == section_label)
        n = chamadas_por_secao.get(section_label, 0) + 1
        chamadas_por_secao[section_label] = n
        if section_label == alvo_titulo and n <= 2:
            # esgota as 2 tentativas configuradas do laço original com
            # texto contaminado — só na 3ª chamada (a tentativa extra que
            # o fix precisa fazer) sai limpo.
            return _section_markdown(section_label, subtitle, CONTAMINADO)
        return _section_markdown(section_label, subtitle, LIMPIO)

    monkeypatch.setattr(engine, "_call_minimax", _fake)

    result = engine.generate_reading(CONTENT_ID, "Mapa Astral Completo", _Profile(), LOCALE, "Lía")

    assert chamadas_por_secao[alvo_titulo] >= 3, "a seção-problema precisa ter recebido uma tentativa extra isolada"
    assert result.source == "minimax", (
        "1 seção com dificuldade temporária não pode jogar fora as outras 14 boas — "
        "esse é o bug real que descartou a leitura 5a769308 em produção"
    )


def test_fail_closed_continua_valendo_quando_a_secao_realmente_nao_sai_limpa(monkeypatch):
    """A trava não pode enfraquecer: se mesmo com a tentativa extra a seção
    continuar contaminada, a leitura tem que sair fallback de verdade."""
    expected = _expected()
    alvo_titulo = expected[3][0]

    def _fake(prompt, locale="pt-BR", max_tokens=None, timeout=None, model=None, section_label=""):
        subtitle = next(s for t, s in expected if t == section_label)
        if section_label == alvo_titulo:
            return _section_markdown(section_label, subtitle, CONTAMINADO)
        return _section_markdown(section_label, subtitle, LIMPIO)

    monkeypatch.setattr(engine, "_call_minimax", _fake)

    result = engine.generate_reading(CONTENT_ID, "Mapa Astral Completo", _Profile(), LOCALE, "Lía")

    assert result.source == "fallback"
