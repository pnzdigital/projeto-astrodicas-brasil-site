"""Guard de escrita não-latina no motor seccionado (`_generate_section`).

Caso real de produção (17/08/2026): geração real do Mapa Astral devolveu
CIRÍLICO no meio do texto — "реали" na seção Sol e "добав" na seção Marte.
O guard existente detectou e refez a seção sozinho, mas o limiar entre
"ruído isolado" (sanitiza e segue) e "outro idioma" (reprova e refaz) não
tinha teste cobrindo o caso real de PALAVRA cirílica — só cobria bloco
grande e caractere solto.

Regra travada aqui (decisão da dona, não reabrir):
- caractere solto = SANITIZA
- palavra/expressão/frase em escrita não-latina = REPROVA e REFAZ
- na dúvida, REFAZ (custo de requisição liberado pela dona)
"""

from __future__ import annotations

from app import engine

CONTENT_ID = "site:content:mapa_astral_completo"


class _Profile:
    def __init__(self):
        self.birth_date = None
        self.birth_time = None
        self.birth_city = ""
        self.birth_country = "BR"
        self.birth_timezone = "America/Sao_Paulo"
        self.birth_latitude = None
        self.birth_longitude = None
        self.partner_name = ""
        self.partner_birth_date = None
        self.partner_birth_time = None
        self.partner_birth_city = ""


def _section_markdown(title: str, subtitle: str, body: str) -> str:
    return f"## {title}\n### {subtitle}\n{body}"


def _generate(monkeypatch, respostas, locale="pt-BR", attempts="8", title="O Sol e sua essência", subtitle="o que move você"):
    monkeypatch.setenv("MINIMAX_API_KEY", "chave-de-teste")
    monkeypatch.setenv("MINIMAX_SECTION_MAX_ATTEMPTS", attempts)
    chamadas = []

    def _fake(prompt, locale_arg, max_tokens=None, timeout=None, model=None, section_label=None):
        chamadas.append(prompt)
        idx = len(chamadas) - 1
        body = respostas[min(idx, len(respostas) - 1)]
        return _section_markdown(title, subtitle, body)

    monkeypatch.setattr(engine, "_call_minimax", _fake)
    section, caiu_no_fallback = engine._generate_section(
        CONTENT_ID, "Mapa Astral Completo", title, subtitle, 1, 15,
        ["A Lua e suas emoções"], {"nome": "Teste"}, locale, _Profile(),
    )
    return section, caiu_no_fallback, chamadas


# --- fronteira 1: caractere solto sanitiza, leitura sai --------------------

def test_ideograma_solto_e_sanitizado_e_a_leitura_sai(monkeypatch):
    sujo = "O Sol em Áries acende a 好vontade de começar. Nada segura esse impulso."
    section, caiu_no_fallback, chamadas = _generate(monkeypatch, [sujo])

    assert len(chamadas) == 1, "caractere isolado não pode custar uma segunda chamada"
    assert not caiu_no_fallback
    assert not engine._has_foreign_script(section["content"])
    assert "vontade de começar" in section["content"]


# --- fronteira 2: palavra/frase em chinês reprova e força nova tentativa ---

def test_palavra_chinesa_reprova_e_forca_nova_tentativa(monkeypatch):
    contaminado = "Esse posicionamento sugere que成长 pessoal acontece devagar."
    limpo = "Esse posicionamento sugere que o crescimento pessoal acontece devagar."
    section, caiu_no_fallback, chamadas = _generate(monkeypatch, [contaminado, limpo])

    assert len(chamadas) == 2, "palavra chinesa é outro idioma: tem que refazer, não sanitizar"
    assert not caiu_no_fallback
    assert not engine._has_foreign_script(section["content"])
    assert "crescimento pessoal" in section["content"]


# --- fronteira 3: cirílico real de produção reprova -------------------------

def test_cirilico_real_de_producao_reprova_secao_sol():
    # "реали" na seção Sol, observado em geração real (17/08/2026).
    trecho = "O Sol em Áries traz vontade de реали zar seus projetos com coragem."
    assert engine._has_foreign_script(trecho)
    _, aproveitavel = engine._sanitize_foreign_script(trecho)
    assert not aproveitavel, "palavra cirílica não é ruído isolado — tem que reprovar"


def test_cirilico_real_de_producao_reprova_secao_marte(monkeypatch):
    # "добав" na seção Marte, observado em geração real (17/08/2026).
    contaminado = "Marte impulsiona você a добав ar mais disciplina à rotina diária."
    limpo = "Marte impulsiona você a somar mais disciplina à rotina diária."
    section, caiu_no_fallback, chamadas = _generate(
        monkeypatch, [contaminado, limpo], title="Marte e sua energia", subtitle="como você age",
    )

    assert len(chamadas) == 2
    assert not caiu_no_fallback
    assert not engine._has_foreign_script(section["content"])


# --- fronteira 4: texto legítimo pt-BR e es-AR não reprova ------------------

def test_pt_br_limpo_com_venus_libra_ascendente_nao_reprova(monkeypatch):
    limpo = (
        "Vênus em Libra ilumina o Ascendente e favorece vínculos afetivos mais "
        "leves. A energia pede equilíbrio entre dar e receber nesta fase."
    )
    section, caiu_no_fallback, chamadas = _generate(monkeypatch, [limpo])

    assert len(chamadas) == 1
    assert not caiu_no_fallback
    assert section["content"] == limpo


def test_es_ar_legitimo_nao_reprova(monkeypatch):
    legitimo = (
        "El sextil entre Venus y Marte favorece decisiones afectivas, aunque "
        "también pide paciencia hacia el final de la semana."
    )
    section, caiu_no_fallback, chamadas = _generate(monkeypatch, [legitimo], locale="es-AR")

    assert len(chamadas) == 1
    assert not caiu_no_fallback
    assert section["content"] == legitimo


# --- fronteira 5: erra nas primeiras, acerta na última = leitura boa -------

def test_secao_erra_nas_primeiras_e_acerta_na_ultima_tentativa(monkeypatch):
    limpo = "O Sol em Áries acende a vontade de começar. Nada segura esse impulso."
    respostas = [
        "Esse posicionamento sugere que成长 pessoal acontece devagar.",
        "Um aspecto que pode estar стимулируя mudanças na sua rotina.",
        "A natureza já حساسة do Ascendente em Câncer pede cuidado.",
        limpo,
    ]
    section, caiu_no_fallback, chamadas = _generate(monkeypatch, respostas, attempts="4")

    assert len(chamadas) == 4
    assert not caiu_no_fallback, "acertou na última tentativa: não pode cair em fallback"
    assert section["content"] == limpo


def test_ultima_tentativa_varia_a_abordagem_quando_mesmo_motivo_repete(monkeypatch):
    """Repetir o mesmo motivo de reprovação 2x muda o texto de correção
    enviado na última tentativa — instrução mais explícita, não o aviso
    genérico de novo (item 4 da tarefa)."""
    contaminado = "Esse posicionamento sugere que成长 pessoal acontece devagar."
    monkeypatch.setenv("MINIMAX_API_KEY", "chave-de-teste")
    monkeypatch.setenv("MINIMAX_SECTION_MAX_ATTEMPTS", "3")
    prompts = []

    def _fake(prompt, locale_arg, max_tokens=None, timeout=None, model=None, section_label=None):
        prompts.append(prompt)
        return _section_markdown("O Sol e sua essência", "o que move você", contaminado)

    monkeypatch.setattr(engine, "_call_minimax", _fake)
    engine._generate_section(
        CONTENT_ID, "Mapa Astral Completo", "O Sol e sua essência", "o que move você", 1, 15,
        ["A Lua e suas emoções"], {"nome": "Teste"}, "pt-BR", _Profile(),
    )

    assert len(prompts) == 3
    # A 2ª tentativa já leva correção citando o erro; a 3ª (última) precisa
    # ser MAIS explícita que a 2ª, não idêntica.
    assert prompts[1] != prompts[0]
    assert prompts[2] != prompts[1]
    assert "ATENÇÃO" in prompts[2] or "já falhou" in prompts[2]
