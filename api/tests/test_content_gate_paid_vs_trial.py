"""Gate de conteúdo: Guia do Mês e Previsão Semanal exigem Diário Astral PAGO.

Quatro cenários obrigatórios:
(a) Comprou só o Mapa Astral avulso → NÃO abre Guia do Mês nem Previsão.
(b) Está em trial (source="trial") → NÃO abre nenhum dos dois.
(c) Comprou o Diário Astral (30 dias pago) → ABRE os dois.
(d) Trial que virou pago → ABRE os dois.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Entitlement
from conftest import create_user, register as _register

GUIA = "site:content:guia_do_mes"
PREVISAO = "site:content:previsao_semanal"


def _register_with_profile(client, email):
    r = _register(client, email, "senha-test")
    assert r.status_code == 200, r.text
    client.put(
        "/api/me/profile",
        json={"birth_date": "1990-05-20", "birth_time": "14:30:00", "birth_city": "Recife", "birth_country": "BR"},
    )


def _grant_via_webhook(client, email, product_id, event_suffix=""):
    r = client.post(
        "/api/webhooks/cakto",
        json={"event_id": f"evt-{event_suffix or product_id}-{email}", "email": email, "product_id": product_id},
    )
    assert r.status_code == 200, r.text


def _fake_generate(monkeypatch):
    from app import main as m
    from app.engine import ReadingResult

    def _fake(content_id, title, profile, locale, customer_name="", on_section_done=None):
        return ReadingResult(body_html="<p>ok</p>", source="minimax")

    monkeypatch.setattr(m, "generate_reading", _fake)


# (a) Mapa Astral avulso: site:mapa_astral concedido, mas NÃO site:diario_astral
def test_mapa_astral_avulso_nao_libera_guia_nem_previsao(client, monkeypatch):
    _fake_generate(monkeypatch)
    _register_with_profile(client, "avulso@test.com")
    _grant_via_webhook(client, "avulso@test.com", "site:mapa_astral")

    assert client.post(f"/api/me/readings/{GUIA}/generate").status_code == 403
    assert client.post(f"/api/me/readings/{PREVISAO}/generate").status_code == 403


# (b) Trial ativo: source="trial" → não abre conteúdo pago
def test_trial_nao_libera_guia_nem_previsao(client, monkeypatch, db_session):
    _fake_generate(monkeypatch)
    _register_with_profile(client, "trial@test.com")

    # Cria entitlement de trial manualmente (igual ao que /api/trial/start faria)
    expires = datetime.now(timezone.utc) + timedelta(days=3)
    user = db_session.scalar(select(Entitlement).where(Entitlement.product_id == "site:diario_astral")).user_id if False else None
    from app.models import User
    user = db_session.scalar(select(User).where(User.email == "trial@test.com"))
    assert user is not None
    db_session.add(Entitlement(
        user_id=user.id,
        product_id="site:diario_astral",
        status="available",
        source="trial",
        expires_at=expires,
    ))
    db_session.commit()

    assert client.post(f"/api/me/readings/{GUIA}/generate").status_code == 403
    assert client.post(f"/api/me/readings/{PREVISAO}/generate").status_code == 403


# (c) Diário Astral pago (source="site") → ABRE guia e previsão
def test_diario_astral_pago_libera_guia_e_previsao(client, monkeypatch):
    _fake_generate(monkeypatch)
    _register_with_profile(client, "pago@test.com")
    _grant_via_webhook(client, "pago@test.com", "site:diario_astral")

    assert client.post(f"/api/me/readings/{GUIA}/generate").status_code == 202
    assert client.post(f"/api/me/readings/{PREVISAO}/generate").status_code in (200, 202)


# (d) Trial que virou pago: adiciona entitlement pago → ABRE
def test_trial_convertido_em_pago_libera_guia_e_previsao(client, monkeypatch, db_session):
    _fake_generate(monkeypatch)
    _register_with_profile(client, "convertido@test.com")

    from app.models import User
    user = db_session.scalar(select(User).where(User.email == "convertido@test.com"))
    assert user is not None

    # Trial: source="trial", expira em 3 dias
    expires_trial = datetime.now(timezone.utc) + timedelta(days=3)
    db_session.add(Entitlement(
        user_id=user.id,
        product_id="site:diario_astral",
        status="available",
        source="trial",
        expires_at=expires_trial,
    ))
    db_session.commit()

    # Ainda trial → bloqueado
    assert client.post(f"/api/me/readings/{GUIA}/generate").status_code == 403

    # Pagou: webhook concede entitlement pago (source="site")
    _grant_via_webhook(client, "convertido@test.com", "site:diario_astral", event_suffix="paid")

    # Agora pago → liberado
    assert client.post(f"/api/me/readings/{GUIA}/generate").status_code == 202
    assert client.post(f"/api/me/readings/{PREVISAO}/generate").status_code in (200, 202)
