"""Ideograma solto não pode custar a leitura inteira; e o retry precisa corrigir.

Caso real (15/08/2026): o Mapa Astral Completo da Luciola fechou 15/15 seções
com `body_html` vazio e job `failed` com `fail_closed`. O motivo é o laço de
tentativas do `_generate_section`: quando o MiniMax devolve UM ideograma solto
no meio de um parágrafo pt-BR perfeito, o guard `_has_foreign_script` descarta
a seção inteira e refaz — reenviando EXATAMENTE o mesmo prompt, sem dizer ao
modelo o que deu errado. Cinco tentativas idênticas depois, cai em fallback.

O que este arquivo trava:
- caractere fora do alfabeto latino, isolado e raro, é CORTADO (não descarta)
- bloco grande de caractere estrangeiro continua reprovando (é outro idioma)
- palavra em inglês NUNCA é cortada: remover "insights" muda o sentido da frase
- o prompt de retry cita o erro observado, para o modelo não repetir
- a regra de idioma diz explicitamente para usar só caracteres latinos
"""

from __future__ import annotations

from app.engine import (
    _has_foreign_script,
    _language_lock_text,
    _sanitize_foreign_script,
    _section_prompt,
)

LIMPO = "O Sol em Áries acende a vontade de começar. Nada segura esse impulso."


def test_ideograma_solto_e_cortado_e_o_texto_aproveitado():
    sujo = "O Sol em Áries acende a 好vontade de começar. Nada segura esse impulso."

    limpo, ok = _sanitize_foreign_script(sujo)

    assert ok, "um ideograma solto em texto pt-BR válido não pode descartar a seção"
    assert not _has_foreign_script(limpo), f"sobrou caractere estrangeiro: {limpo!r}"
    assert "vontade de começar" in limpo


def test_texto_limpo_passa_intacto():
    limpo, ok = _sanitize_foreign_script(LIMPO)

    assert ok
    assert limpo == LIMPO


def test_bloco_grande_em_outro_alfabeto_continua_reprovando():
    outro_idioma = "Солнце в Овне зажигает волю начинать заново, и ничто не остановит этот импульс."

    _, ok = _sanitize_foreign_script(outro_idioma)

    assert not ok, "texto inteiro em outro alfabeto é outro idioma, não ruído — tem que reprovar"


def test_muitos_caracteres_estrangeiros_reprovam():
    sujo = LIMPO + " 好 运 来 了 今 天"

    _, ok = _sanitize_foreign_script(sujo)

    assert not ok, "seis ideogramas não são ruído isolado; refazer a seção"


def test_regra_de_idioma_proibe_caractere_nao_latino():
    texto = _language_lock_text("pt-BR").lower()

    assert "latino" in texto, "a regra de idioma nunca falou de alfabeto; o modelo não sabe da proibição"
    assert "ideograma" in texto or "ideogramas" in texto


def _prompt(correction=None) -> str:
    return _section_prompt(
        "site:content:mapa_astral_completo",
        "Mapa Astral Completo",
        "O Sol e sua essência",
        "o que move você",
        1,
        15,
        ["O Sol e sua essência", "A Lua e suas emoções"],
        {"nome": "Teste"},
        "pt-BR",
        correction=correction,
    )


def test_retry_cita_o_erro_observado():
    base = _prompt()
    corrigido = _prompt(correction="a resposta anterior trouxe o caractere 好")

    assert "好" not in base, "o prompt base não deve carregar correção nenhuma"
    assert "好" in corrigido, (
        "o retry reenviava o prompt idêntico; sem citar o erro o modelo repete o mesmo erro"
    )
    assert len(corrigido) > len(base)
