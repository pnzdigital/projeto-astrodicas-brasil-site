"""Trava de regressão: mapa_do_amor_sinastria e mapa_da_prosperidade eram
entregues no site como parágrafo corrido (9-12 / 8-11 parágrafos) enquanto o
mesmo produto pago no bot do Telegram usa 14 seções cada — metade do
conteúdo pelo mesmo preço. Ver app/engine.py SECTIONS_BY_CONTENT_ID e
SINASTRIA_SEM_PARCEIRO_SECTIONS.
"""
from datetime import date

from app import engine


class _Profile:
    def __init__(self, partner_birth_date=None):
        self.birth_date = date(1990, 3, 15)
        self.birth_time = None
        self.birth_city = "São Paulo"
        self.birth_country = "BR"
        self.birth_timezone = "America/Sao_Paulo"
        self.birth_latitude = None
        self.birth_longitude = None
        self.partner_name = "Ana" if partner_birth_date else ""
        self.partner_birth_date = partner_birth_date
        self.partner_birth_time = None
        self.partner_birth_city = "Rio de Janeiro" if partner_birth_date else ""


def test_mapa_da_prosperidade_esta_seccionado():
    sections = engine.sections_for("site:content:mapa_da_prosperidade")
    assert sections, "prosperidade precisa estar em SECTIONS_BY_CONTENT_ID"
    assert len(sections) == 14
    assert sections[0][0] == "Introdução à Prosperidade"
    assert sections[-1][0] == "Mensagem Final"


def test_mapa_do_amor_com_parceiro_usa_lista_com_parceiro():
    profile = _Profile(partner_birth_date=date(1991, 7, 20))
    sections = engine.sections_for("site:content:mapa_do_amor_sinastria", profile)
    assert sections == engine.SECTIONS_BY_CONTENT_ID["site:content:mapa_do_amor_sinastria"]
    assert sections[0][0] == "Introdução à Sinastria"


def test_mapa_do_amor_sem_parceiro_usa_lista_sem_parceiro():
    profile = _Profile(partner_birth_date=None)
    sections = engine.sections_for("site:content:mapa_do_amor_sinastria", profile)
    assert sections == engine.SINASTRIA_SEM_PARCEIRO_SECTIONS
    assert sections[0][0] == "Guia Amoroso Pessoal"
    # Não pode misturar títulos de parceiro na variante sem parceiro.
    titles = {t for t, _ in sections}
    assert "Vênus em Compatibilidade" not in titles


def test_mapa_do_amor_sem_profile_cai_na_variante_sem_parceiro():
    sections = engine.sections_for("site:content:mapa_do_amor_sinastria", None)
    assert sections == engine.SINASTRIA_SEM_PARCEIRO_SECTIONS


def test_prompt_sem_parceiro_nao_pede_mais_paragrafo_corrido():
    """Regressão do bug de contradição: quando o content_id entra em
    SECTIONS_BY_CONTENT_ID, a regra antiga de parágrafo corrido não pode
    continuar valendo no mesmo prompt (duas instruções de formato brigando)."""
    profile = _Profile(partner_birth_date=None)
    prompt = engine._prompt(
        "site:content:mapa_do_amor_sinastria", "Mapa do Amor", profile, "pt-BR",
    )
    assert "ESTRUTURADA EM SEÇÕES" in prompt
    assert "9 a 12 parágrafos" not in prompt
    assert "não invente posições planetárias dele" in prompt
