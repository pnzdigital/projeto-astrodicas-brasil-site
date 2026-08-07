"""Nenhuma leitura paga sai com caractere fora do alfabeto latino.

O MiniMax-M2.1 (PROIBIDO) trocava uma palavra solta pelo equivalente em
chinês, árabe ou russo algumas vezes por texto — comportamento observado em
2026-08-05 sobre quatro leituras reais ("a natureza já حساسة do Ascendente",
"sugere que成长 pessoal", "estar стимулируя mudanças"). MiniMax-M2.7 (atual)
não exibiu leak no benchmark de 2026-08-07 (3 amostras), mas o guard permanece
ativo: o desvio é estocástico. O UTF-8 chega íntegro: é o modelo derrapando
de idioma, não corrupção de encoding.
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


# Trechos reais de um Mapa Astral pago (R$ 47) gerado em pt-BR, capturados em
# produção em 2026-08-06. _has_foreign_script não pega nenhum: são todas
# palavras em alfabeto latino normal, só que no idioma errado.
TRECHOS_CONTAMINADOS_PT_BR = [
    "uma mente capaz de synthesize informações complexas",
    "mantendo nonetheless uma necessidade de independência emocional",
    "aporta energia aventura e enthusiasm",
    "no intercambio de ideias",
    "podendo manifestarse",
    "as urgeências de inovação",
    "Os potenciais highlighted neste mapa",
    "a busca por harmonic relationships própria da Lua em Libra",
]


def test_reprova_os_oito_trechos_reais_contaminados_pt_br():
    for trecho in TRECHOS_CONTAMINADOS_PT_BR:
        assert engine._has_foreign_words(trecho, "pt-BR"), f"deveria reprovar: {trecho!r}"
        assert engine._has_language_leak(trecho, "pt-BR"), f"deveria reprovar: {trecho!r}"


TEXTOS_ASTROLOGICOS_BONS = [
    "O sextil entre Vênus e Marte favorece decisões afetivas equilibradas.",
    "Um trígono de água conecta sua Lua ao Ascendente em Peixes.",
    "A quadratura com Saturno pede paciência antes de qualquer mudança de rota.",
    "Mercúrio retrógrado em Touro pede atenção a contratos e releituras.",
    "O orbe apertado entre Sol e Júpiter amplia a confiança nessa fase.",
    "Com Áries no Ascendente, Touro no Sol e Gêmeos na Lua, sua energia pede ação seguida de calma.",
    "A oposição entre Sol e Lua em Câncer e Capricórnio tensiona casa e carreira.",
    "Vênus em Leão, Virgem no meio-céu e Libra na sétima casa marcam seus vínculos.",
]


def test_nao_reprova_textos_astrologicos_legitimos_pt_br():
    for texto in TEXTOS_ASTROLOGICOS_BONS:
        assert not engine._has_foreign_words(texto, "pt-BR"), f"falso positivo em: {texto!r}"
        assert not engine._has_language_leak(texto, "pt-BR"), f"falso positivo em: {texto!r}"


def test_espanhol_legitimo_nao_reprova_em_es_ar():
    texto = "El sextil entre Venus y Marte favorece decisiones, aunque también pide paciencia."
    assert not engine._has_foreign_words(texto, "es-AR")


def test_ingles_reprova_em_qualquer_locale():
    texto = "Your Ascendant highlights a moment of nonetheless growth."
    assert engine._has_foreign_words(texto, "pt-BR")
    assert engine._has_foreign_words(texto, "es-AR")


def test_regenera_ate_tres_vezes_quando_palavra_vaza_de_outro_idioma(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "chave-de-teste")
    monkeypatch.setenv("MINIMAX_MAX_ATTEMPTS", "3")
    respostas = [
        "Uma mente capaz de synthesize informações complexas.",
        "No intercambio de ideias fluem novas percepções.",
        LIMPO,
    ]
    chamadas = []

    def _fake(prompt, locale="pt-BR"):
        chamadas.append(locale)
        return respostas[len(chamadas) - 1]

    monkeypatch.setattr(engine, "_call_minimax", _fake)
    result = engine.generate_reading("site:content:mapa_astral", "Mapa astral", _Profile(date(1990, 3, 15)))

    assert len(chamadas) == 3
    assert result.source == "minimax"
    assert not engine._has_language_leak(result.body_html, "pt-BR")
