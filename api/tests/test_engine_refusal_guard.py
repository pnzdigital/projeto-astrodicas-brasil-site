"""Guard de RECUSA/META-TEXTO no motor seccionado (`_generate_section`).

Achado real de QA (20 gerações no commit 8a79304, previsao_semanal pt-BR):
o modelo devolveu uma RECUSA ("não posso ajudar com isso") e ela foi
persistida e entregue como leitura paga. Nenhum guard existente cobria esse
caso: fail_closed só protege o fallback editorial nosso, o guard de script
só pega alfabeto errado, o guard de palavra vazada só pega troca de idioma.
A recusa está em idioma e alfabeto corretos — ela fala SOBRE a tarefa em vez
de executá-la.

Regra travada aqui:
- recusa/meta-comentário no início da seção = REPROVA e REFAZ (nunca sanitiza:
  não dá para "consertar" uma recusa cortando pedaço dela)
- seção anormalmente curta (abaixo de `_MIN_SECTION_CHARS`) = REPROVA e REFAZ
- se TODAS as tentativas falharem, o comportamento é o mesmo do fail_closed:
  a seção cai no fallback editorial pontual, nunca persiste a recusa
"""

from __future__ import annotations

from app import engine

CONTENT_ID = "site:content:previsao_semanal"


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


def _generate(monkeypatch, respostas, locale="pt-BR", attempts="8", title="Panorama da Semana", subtitle="O clima geral dos 7 dias"):
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
        CONTENT_ID, "Previsão Semanal", title, subtitle, 1, 7,
        ["Segunda e Terça"], {"nome": "Teste"}, locale, _Profile(),
    )
    return section, caiu_no_fallback, chamadas


_LEITURA_LEGITIMA = (
    "Esta semana pede atenção ao ritmo interno antes de qualquer decisão externa. "
    "Vênus em trânsito favorece conversas mais sinceras, enquanto a Lua traz uma "
    "vontade de recolhimento nos primeiros dias. Aproveite o meio da semana para "
    "avançar no que já estava encaminhado, sem pressa de resolver tudo de uma vez."
)


# --- unidade: função de detecção -------------------------------------------

def test_recusa_pt_br_e_detectada():
    recusa = "Desculpe, mas não posso ajudar com isso."
    assert engine._looks_like_refusal(recusa)


def test_recusa_es_ar_e_detectada():
    recusa = "Lo siento, pero no puedo ayudar con esto."
    assert engine._looks_like_refusal(recusa)


def test_meta_comentario_como_ia_e_detectado():
    meta = "Como modelo de linguagem, não tenho acesso a dados astrológicos em tempo real."
    assert engine._looks_like_refusal(meta)


def test_pedido_de_mais_dados_e_detectado():
    pedido = "Preciso que você me informe a hora exata de nascimento para continuar."
    assert engine._looks_like_refusal(pedido)


def test_ressalva_editorial_legitima_nao_e_recusa():
    ressalva = (
        "Nenhum mapa determina seu futuro: ele mostra tendências, não um caminho fixo. "
        + _LEITURA_LEGITIMA
    )
    assert not engine._looks_like_refusal(ressalva)


def test_sinto_em_sentido_astrologico_nao_e_recusa():
    texto = (
        "Você sinte que algo pede mudança nesta semana, e Marte reforça esse impulso. "
        + _LEITURA_LEGITIMA
    )
    assert not engine._looks_like_refusal(texto)


def test_texto_normal_longo_nao_e_recusa():
    assert not engine._looks_like_refusal(_LEITURA_LEGITIMA)


def test_secao_curta_demais_e_detectada():
    curta = "Esta semana traz boas energias."
    assert engine._looks_too_short(curta)


def test_secao_com_tamanho_normal_nao_e_curta():
    assert not engine._looks_too_short(_LEITURA_LEGITIMA)


# --- integração: laço de _generate_section refaz e não persiste a recusa ---

def test_recusa_reprova_e_forca_nova_tentativa(monkeypatch):
    recusa = "Desculpe, mas não posso ajudar com isso."
    section, caiu_no_fallback, chamadas = _generate(monkeypatch, [recusa, _LEITURA_LEGITIMA])

    assert len(chamadas) == 2, "recusa tem que forçar nova tentativa, nunca ser aceita de primeira"
    assert not caiu_no_fallback
    assert not engine._looks_like_refusal(section["content"])
    assert "ritmo interno" in section["content"]


def test_recusa_es_ar_reprova_e_forca_nova_tentativa(monkeypatch):
    recusa = "Lo siento, pero no puedo ayudar con esto."
    limpo_es = (
        "Esta semana pide atención al ritmo interno antes de cualquier decisión externa. "
        "Venus en tránsito favorece conversaciones más sinceras, mientras la Luna trae una "
        "necesidad de recogimiento en los primeros días de la semana."
    )
    section, caiu_no_fallback, chamadas = _generate(monkeypatch, [recusa, limpo_es], locale="es-AR")

    assert len(chamadas) == 2
    assert not caiu_no_fallback
    assert not engine._looks_like_refusal(section["content"])


def test_secao_curta_demais_reprova_e_forca_nova_tentativa(monkeypatch):
    curta = "Esta semana traz boas energias."
    section, caiu_no_fallback, chamadas = _generate(monkeypatch, [curta, _LEITURA_LEGITIMA])

    assert len(chamadas) == 2, "seção curta demais tem que forçar nova tentativa"
    assert not caiu_no_fallback
    assert "ritmo interno" in section["content"]


def test_recusa_persistente_cai_no_fallback_editorial_sem_persistir_a_recusa(monkeypatch):
    # Todas as tentativas recusam: mesmo comportamento do fail_closed — a
    # seção cai no fallback pontual, a recusa NUNCA chega ao HTML entregue.
    recusa = "Desculpe, mas não posso ajudar com isso."
    section, caiu_no_fallback, chamadas = _generate(
        monkeypatch, [recusa], attempts="3",
    )

    assert caiu_no_fallback, "esgotadas as tentativas em recusa, tem que cair no fallback — nunca persistir a recusa"
    assert not engine._looks_like_refusal(section["content"])
    assert "não posso ajudar" not in section["content"].lower()
