"""PDF do conteúdo sem seções: o catálogo promete download por padrão para os
três bônus pagos (calendário lunar, guia dos retrógrados, manual do
ascendente), mas o backend recusava gerar PDF quando a leitura não tinha
seções (main.py, ~linha 736) — cliente pagante clicava "Baixar PDF" e não
recebia nada. ``build_pdf`` agora aceita ``body_html`` (formato de parágrafo
corrido) como alternativa a ``sections``.
"""

from __future__ import annotations

import pytest

from app.pdf_export import build_pdf


def _pdf_text_bytes(pdf_bytes: bytes) -> bool:
    return pdf_bytes.startswith(b"%PDF")


def test_build_pdf_from_sections_still_works():
    sections = [
        {"title": "Introdução", "subtitle": "Seu mapa de alma", "content": "Parágrafo um.\n\nParágrafo dois."},
        {"title": "Sol", "subtitle": "Identidade", "content": "Outro parágrafo aqui."},
    ]

    pdf_bytes = build_pdf("Mapa Astral Completo", sections, customer_name="Cliente Teste")

    assert _pdf_text_bytes(pdf_bytes)
    assert len(pdf_bytes) > 1000


def test_build_pdf_from_body_html_without_sections():
    """Bug corrigido: leitura sem seções (calendário lunar, guia dos
    retrógrados, manual do ascendente) precisa gerar PDF de verdade, em
    parágrafo corrido, e não mais levantar 422."""
    body_html = (
        "<p>Primeiro parágrafo do calendário lunar, bem substancioso.</p>"
        "<p>Segundo parágrafo, com mais conteúdo sobre as fases da lua.</p>"
        "<p>Terceiro parágrafo fechando o guia editorial.</p>"
    )

    pdf_bytes = build_pdf("Calendário Lunar", sections=[], customer_name="Cliente Teste", body_html=body_html)

    assert _pdf_text_bytes(pdf_bytes)
    assert len(pdf_bytes) > 1000


def test_build_pdf_empty_content_raises_instead_of_blank_pdf():
    """Nem seções, nem body_html: tem que falhar de forma clara — nunca
    devolver um arquivo "PDF" vazio/quebrado pro cliente pagante."""
    with pytest.raises(ValueError):
        build_pdf("Título Qualquer", sections=[], customer_name="Cliente Teste", body_html="")


def test_build_pdf_empty_body_html_paragraphs_raises():
    """body_html presente mas sem nenhum <p> com conteúdo (ex.: geração
    falhou e devolveu string vazia/whitespace) também não pode virar PDF."""
    with pytest.raises(ValueError):
        build_pdf("Título Qualquer", sections=[], customer_name="Cliente Teste", body_html="<p>   </p>")
