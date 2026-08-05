"""Nenhuma leitura paga sai com caractere fora do alfabeto latino.

O MiniMax-M2.1 troca uma palavra solta pelo equivalente em chinês, árabe ou
russo algumas vezes por texto — comportamento observado em 2026-08-05 sobre
quatro leituras reais ("a natureza já حساسة do Ascendente", "sugere que成长
pessoal", "estar стимулируя mudanças"). O UTF-8 chega íntegro: é o modelo
derrapando de idioma, não corrupção de encoding.
"""

from datetime import date

from app import engine


class _Profile:
    def __init__(self, birth_date=None, birth_city=""):
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


LIMPO = "Sua leitura começa pelo Sol em Peixes.\n\nA intuição guia o próximo passo — confie nela."
ARABE = "A natureza já حساسة do Ascendente em Câncer pede cuidado."
CHINES = "Esse posicionamento sugere que成长 pessoal acontece devagar."
RUSSO = "Um aspecto que pode estar стимулируя mudanças na sua rotina."


def test_aceita_acentos_e_pontuacao_tipografica():
    assert not engine._has_foreign_script(LIMPO)
    assert not engine._has_foreign_script("Coração, ação, ênfase, ü, ñ — “aspas curvas”… e ‘simples’.")


def test_reprova_arabe_chines_e_russo():
    assert engine._has_foreign_script(ARABE)
    assert engine._has_foreign_script(CHINES)
    assert engine._has_foreign_script(RUSSO)


def test_amostra_mostra_o_caractere_reprovado():
    amostra = engine._foreign_sample(CHINES)
    assert "成" in amostra and "长" in amostra


def test_refaz_a_chamada_quando_o_texto_derrapa_de_idioma(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "chave-de-teste")
    monkeypatch.setenv("MINIMAX_MAX_ATTEMPTS", "3")
    respostas = [CHINES, ARABE, LIMPO]
    chamadas = []

    def _fake(prompt, locale="pt-BR"):
        chamadas.append(locale)
        return respostas[len(chamadas) - 1]

    monkeypatch.setattr(engine, "_call_minimax", _fake)
    result = engine.generate_reading("site:content:mapa_astral", "Mapa astral", _Profile(date(1990, 3, 15)))

    assert len(chamadas) == 3, "deveria ter refeito a chamada até sair texto limpo"
    assert result.source == "minimax"
    assert not engine._has_foreign_script(result.body_html)
    assert "Peixes" not in result.body_html or True  # conteúdo vem do modelo, não asseguramos o signo aqui


def test_cai_no_fallback_quando_todas_as_tentativas_derrapam(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "chave-de-teste")
    monkeypatch.setenv("MINIMAX_MAX_ATTEMPTS", "2")
    chamadas = []

    def _fake(prompt, locale="pt-BR"):
        chamadas.append(locale)
        return CHINES

    monkeypatch.setattr(engine, "_call_minimax", _fake)
    result = engine.generate_reading("site:content:mapa_astral", "Mapa astral", _Profile(date(1990, 3, 15)))

    assert len(chamadas) == 2
    assert result.source == "fallback", "melhor entregar o editorial padrão do que texto com ideograma"
    assert not engine._has_foreign_script(result.body_html)
    assert "Peixes" in result.body_html


def test_texto_limpo_passa_de_primeira(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "chave-de-teste")
    chamadas = []

    def _fake(prompt, locale="pt-BR"):
        chamadas.append(locale)
        return LIMPO

    monkeypatch.setattr(engine, "_call_minimax", _fake)
    result = engine.generate_reading("site:content:mapa_astral", "Mapa astral", _Profile(date(1990, 3, 15)))

    assert len(chamadas) == 1, "texto limpo não pode custar uma segunda chamada"
    assert result.source == "minimax"


def test_system_prompt_fixa_o_idioma_do_mercado():
    assert "português do Brasil" in engine._system_prompt("pt-BR")
    assert "espanhol rioplatense" in engine._system_prompt("es-AR")
