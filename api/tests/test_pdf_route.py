"""Rota /api/me/readings/{content_id}/pdf: cobre o bug corrigido de recusa de
PDF para conteúdo sem seções (calendário lunar, guia dos retrógrados, manual
do ascendente — os três bônus pagos do Diário Astral, ver catálogo)."""

from __future__ import annotations

from conftest import register as _register


def _register_and_pay_diario_completo(client, email="bonus@example.com"):
    response = _register(client, email, "senha-segura", name="Cliente Bônus", locale="pt-BR")
    assert response.status_code == 200, response.text
    payload = {
        "event_id": f"evt-{email}",
        "email": email,
        "product_id": "site:diario_astral_completo",
    }
    granted = client.post("/api/webhooks/cakto", json=payload)
    assert granted.status_code == 200, granted.text


def _generate_and_wait(client, content_id):
    generated = client.post(f"/api/me/readings/{content_id}/generate")
    assert generated.status_code == 202, generated.text
    listed = client.get("/api/me/readings")
    assert listed.status_code == 200
    return next(r for r in listed.json()["readings"] if r["content_id"] == content_id)


def test_pdf_downloads_for_unsectioned_bonus_content(client, monkeypatch):
    """Bug: calendário lunar (sem seções) devolvia 422 em vez do PDF que o
    catálogo promete. Agora precisa devolver um PDF de verdade."""
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    _register_and_pay_diario_completo(client)
    client.put(
        "/api/me/profile",
        json={"birth_date": "1990-05-20", "birth_time": "12:30:00", "birth_city": "Recife", "birth_country": "BR"},
    )

    _generate_and_wait(client, "site:content:calendario_lunar")

    pdf_response = client.get("/api/me/readings/site:content:calendario_lunar/pdf")

    assert pdf_response.status_code == 200, pdf_response.text
    assert pdf_response.headers["content-type"] == "application/pdf"
    assert pdf_response.content.startswith(b"%PDF")


def test_pdf_still_works_for_sectioned_content(client, monkeypatch):
    """Regressão: mapa astral completo (seccionado) continua gerando PDF."""
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    response = _register(client, "sectioned@example.com", "senha-segura", name="Cliente Astral", locale="pt-BR")
    assert response.status_code == 200, response.text
    granted = client.post(
        "/api/webhooks/cakto",
        json={"event_id": "evt-sectioned", "email": "sectioned@example.com", "product_id": "site:mapa_astral"},
    )
    assert granted.status_code == 200, granted.text
    client.put(
        "/api/me/profile",
        json={"birth_date": "1990-05-20", "birth_time": "12:30:00", "birth_city": "Recife", "birth_country": "BR"},
    )

    _generate_and_wait(client, "site:content:mapa_astral_completo")

    pdf_response = client.get("/api/me/readings/site:content:mapa_astral_completo/pdf")

    assert pdf_response.status_code == 200, pdf_response.text
    assert pdf_response.content.startswith(b"%PDF")


def test_pdf_404_when_no_reading_exists(client, monkeypatch):
    """Sem leitura gerada nenhuma, continua 404 (não vira 422/500)."""
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    response = _register(client, "sem-leitura@example.com", "senha-segura", name="Cliente Sem Leitura", locale="pt-BR")
    assert response.status_code == 200, response.text

    pdf_response = client.get("/api/me/readings/site:content:calendario_lunar/pdf")

    assert pdf_response.status_code == 404
