"""Exportação de leituras seccionadas para PDF.

Referência de qualidade: astrodicas-telegram/src/vendas_bot/mapa_premium.py
(``FPDFPremium``, ``PALETAS``) — o bot entrega um PDF de ~14 páginas com capa
personalizada, uma seção por página e paleta por tipo de mapa. Este módulo não
copia aquele arquivo (é somente leitura), mas replica o mesmo nível de
acabamento com a lib disponível no site: fpdf2 (pacote ``fpdf``), que já é
puro Python — sem dependência de compilador nativo — diferente do que o
briefing original presumia (reportlab). fpdf2 já estava instalado no venv do
site (usado por outra dependência) e cobre exatamente o mesmo caso de uso
(FPDF é a classe-base que o próprio bot estende).

Fontes: usamos as fontes core do fpdf2 (Helvetica), que cobrem Latin-1 —
suficiente para pt-BR e es-AR — sem precisar embutir um arquivo .ttf.
"""

from __future__ import annotations

import html
import io
import re

from fpdf import FPDF

PALETTE = {
    "cor_principal": (108, 59, 140),
    "cor_secundaria": (60, 40, 100),
    "cor_terciaria": (180, 160, 220),
    "cor_texto": (245, 240, 255),
    "cor_fundo": (18, 12, 35),
    "cor_destaque": (220, 180, 255),
    "cor_card": (35, 25, 60),
    "cor_tag": (160, 120, 200),
}


class ReadingPDF(FPDF):
    def __init__(self):
        super().__init__()
        self.set_auto_page_break(auto=True, margin=20)

    def footer(self):
        if self.page_no() <= 1:
            return
        self.set_y(-15)
        self.set_fill_color(*PALETTE["cor_card"])
        self.rect(0, self.get_y(), self.w, 15, style="F")
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*PALETTE["cor_tag"])
        self.cell(0, 8, f"AstroDicas | Pag {self.page_no()}", align="C")


def _clean(text: str) -> str:
    """fpdf2 core fonts (Latin-1/cp1252) não têm todo Unicode — remove o resto
    sem quebrar a geração (mesmo espírito do ``_limpar_texto_llm`` do bot)."""
    return text.encode("latin-1", "replace").decode("latin-1")


def _paragraphs_from_html(body_html: str) -> list[str]:
    """Extrai os parágrafos de ``<p>...</p>`` (formato de content_ids não
    seccionados, ver ``Reading.body_html``) de volta para texto puro."""
    return [html.unescape(p).strip() for p in re.findall(r"<p>(.*?)</p>", body_html, re.DOTALL) if p.strip()]


def build_pdf(title: str, sections: list[dict], customer_name: str = "", warning: str = "", body_html: str = "") -> bytes:
    """Gera o PDF a partir de seções {"title", "subtitle", "content"} OU,
    quando ``sections`` está vazio, a partir de ``body_html`` (formato de
    parágrafo corrido usado pelos content_ids fora de SECTIONS_BY_CONTENT_ID —
    ver engine.py). Mesma capa, aviso e paleta nos dois casos; a única
    diferença é que o corpo vira uma única página de parágrafos em vez de
    uma página por seção, já que não há título de seção para ancorar o layout.

    Levanta ``ValueError`` se não houver nem seções nem parágrafos de
    ``body_html`` — nunca devolve um PDF em branco.
    """
    paragraphs_from_body = [] if sections else _paragraphs_from_html(body_html)
    if not sections and not paragraphs_from_body:
        raise ValueError("Sem conteúdo para gerar PDF (nem seções, nem parágrafos).")

    pdf = ReadingPDF()

    # Capa
    pdf.add_page()
    pdf.set_fill_color(*PALETTE["cor_fundo"])
    pdf.rect(0, 0, pdf.w, pdf.h, style="F")
    pdf.set_xy(pdf.l_margin, 90)
    pdf.set_font("Helvetica", "B", 28)
    pdf.set_text_color(*PALETTE["cor_destaque"])
    pdf.multi_cell(0, 14, _clean(title), align="C")
    if customer_name:
        pdf.ln(6)
        pdf.set_font("Helvetica", "", 16)
        pdf.set_text_color(*PALETTE["cor_texto"])
        pdf.cell(0, 10, _clean(customer_name), align="C")
    pdf.ln(10)
    pdf.set_font("Helvetica", "I", 11)
    pdf.set_text_color(*PALETTE["cor_tag"])
    pdf.cell(0, 8, "AstroDicas", align="C")

    if warning:
        pdf.add_page()
        pdf.set_fill_color(*PALETTE["cor_card"])
        pdf.rect(0, 0, pdf.w, pdf.h, style="F")
        pdf.set_xy(pdf.l_margin, 30)
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(*PALETTE["cor_destaque"])
        pdf.multi_cell(0, 8, _clean("Aviso importante"))
        pdf.ln(4)
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "", 12)
        pdf.set_text_color(*PALETTE["cor_texto"])
        pdf.multi_cell(0, 7, _clean(warning))

    if paragraphs_from_body:
        pdf.add_page()
        pdf.set_fill_color(*PALETTE["cor_fundo"])
        pdf.rect(0, 0, pdf.w, pdf.h, style="F")
        pdf.set_xy(pdf.l_margin, 20)
        pdf.set_font("Helvetica", "B", 20)
        pdf.set_text_color(*PALETTE["cor_destaque"])
        pdf.multi_cell(0, 11, _clean(title))
        pdf.ln(4)
        pdf.set_font("Helvetica", "", 12)
        pdf.set_text_color(*PALETTE["cor_texto"])
        for paragraph in paragraphs_from_body:
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(0, 7, _clean(re.sub(r"\s*\n\s*", " ", paragraph)))
            pdf.ln(3)

    for section in sections:
        pdf.add_page()
        pdf.set_fill_color(*PALETTE["cor_fundo"])
        pdf.rect(0, 0, pdf.w, pdf.h, style="F")
        pdf.set_xy(pdf.l_margin, 20)
        pdf.set_font("Helvetica", "B", 20)
        pdf.set_text_color(*PALETTE["cor_destaque"])
        pdf.multi_cell(0, 11, _clean(section.get("title", "")))
        if section.get("subtitle"):
            pdf.set_x(pdf.l_margin)
            pdf.set_font("Helvetica", "I", 13)
            pdf.set_text_color(*PALETTE["cor_terciaria"])
            pdf.multi_cell(0, 8, _clean(section["subtitle"]))
        pdf.ln(4)
        pdf.set_font("Helvetica", "", 12)
        pdf.set_text_color(*PALETTE["cor_texto"])
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", section.get("content", "")) if p.strip()]
        for paragraph in paragraphs:
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(0, 7, _clean(re.sub(r"\s*\n\s*", " ", paragraph)))
            pdf.ln(3)

    return bytes(pdf.output())
