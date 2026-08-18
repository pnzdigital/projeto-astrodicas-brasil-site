"""A fórmula do prompt e do modelo — o que fazia leitura paga cair em fallback.

Medição de 18/08/2026 (Mapa Astral Completo, 15 seções por modelo, mesmo prompt):
MiniMax-M2.7 devolveu escrita não-latina no corpo de 9 das 15 seções; o
MiniMax-Text-01, que o canal do Telegram usa desde sempre, devolveu 0 em 35
seções nos dois idiomas. Estes testes prendem as três decisões que saíram dali.
"""

from datetime import date

from app import engine


class _Profile:
    birth_date = date(1990, 3, 15)
    birth_time = None
    birth_city = "São Paulo"
    birth_country = "BR"
    birth_timezone = "America/Sao_Paulo"
    birth_latitude = None
    birth_longitude = None
    partner_name = ""
    partner_birth_date = None
    partner_birth_time = None
    partner_birth_city = ""


def _prompt(section_title: str, subtitle: str = "Identidade e propósito", locale: str = "pt-BR") -> str:
    context = engine._profile_context(_Profile(), "Luciana")
    context["birth_time_assumed"] = True
    return engine._section_prompt(
        "site:content:mapa_astral_completo", "Mapa Astral Completo", section_title, subtitle,
        2, 15, ["Sol", "Lua", "Ascendente"], context, locale,
    )


def test_modelo_padrao_nao_e_de_raciocinio():
    """M2.x pensa em vários idiomas dentro do <think> e derrapa para o alfabeto
    do raciocínio no meio da frase em português — era a causa da retentativa."""
    assert engine._DEFAULT_MODEL == "MiniMax-Text-01"


def test_tentativas_por_secao_nao_sao_curativo():
    """Tirada a causa, retentativa cobre rede e azar isolado — não esconde
    regressão do fornecedor atrás de nove repetições silenciosas."""
    assert int(engine._SECTION_MAX_ATTEMPTS_DEFAULT) <= 4


def test_aviso_de_hora_assumida_so_vai_para_secao_do_ascendente():
    """Nas outras seções o aviso virava meta-texto: medido em 18/08/2026, a
    seção Sol gastou o terceiro parágrafo explicando o que não pôde calcular."""
    assert "ATENÇÃO: a hora de nascimento" in _prompt("Ascendente", "Como o mundo te vê")
    assert "ATENÇÃO: a hora de nascimento" not in _prompt("Sol")
    assert "ATENÇÃO: a hora de nascimento" not in _prompt("Plutão", "Transformação profunda")


def test_aviso_de_hora_assumida_no_documento_inteiro_nao_muda():
    """Caminho antigo (leitura inteira num prompt só) não passa section_title."""
    assert "ATENÇÃO: a hora de nascimento" in engine._assumed_warning_text("pt-BR", True)
    assert engine._assumed_warning_text("pt-BR", False) == ""


def test_prompt_exige_signo_e_casa_e_proibe_meta_texto():
    """Sem a regra de posição o texto sai genérico de signo solar; sem a
    proibição de meta-texto o modelo comenta o que faltou em vez de interpretar."""
    prompt = _prompt("Sol")
    assert "cite o signo E a casa" in prompt
    assert "NÃO escreva meta-texto" in prompt
