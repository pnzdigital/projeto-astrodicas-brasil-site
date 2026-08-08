"""Horóscopo diário da ASSINANTE do Plano Lua — a promessa central do plano
("horóscopo personalizado todos os dias, calculado sobre sua carta").

Não é uma rota nova: ``POST /api/me/readings/{content_id}/generate`` já lida
com qualquer content_id, inclusive ``site:content:horoscopo_diario``
(entitlement, geração via ``engine.generate_reading`` com o mesmo guard de
idioma da leitura paga, cache em ``Reading``). O que faltava, e este arquivo
prova:

1. O cache usava ``datetime.now(timezone.utc).date()`` — a virada de dia do
   SERVIDOR, não da assinante. Entre 21h e meia-noite no Brasil/Argentina o
   servidor já está amanhã; o cache expirava um dia cedo demais lá e ficava
   preso um dia demais aqui, dependendo do sentido do desvio.
2. O entitlement era checado DEPOIS do cache: uma assinante que cancelasse ou
   vencesse ainda recebia a leitura de hoje se ela já tivesse sido gerada
   antes.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app import main as main_module
from app.engine import ReadingResult
from app.models import Reading
from conftest import register as _register

CONTENT_ID = "site:content:horoscopo_diario"


def _register_with_profile(client, email="lua@example.com", locale="pt-BR", birth_country="BR"):
    response = _register(client, email, "senha-segura", name="Cliente Lua", locale=locale)
    assert response.status_code == 200, response.text
    client.put(
        "/api/me/profile",
        json={"birth_date": "1990-05-20", "birth_time": "14:30:00", "birth_city": "Recife", "birth_country": birth_country},
    )


def _grant_plano_lua(client, email):
    granted = client.post(
        "/api/webhooks/cakto",
        json={"event_id": f"evt-{email}", "email": email, "product_id": "site:plano_lua"},
    )
    assert granted.status_code == 200, granted.text


def _fake_generate_reading(monkeypatch, calls):
    def _fake(content_id, title, profile, locale, customer_name="", on_section_done=None):
        calls.append(content_id)
        return ReadingResult(body_html=f"<p>Horóscopo de teste #{len(calls)}.</p>", source="minimax")

    monkeypatch.setattr(main_module, "generate_reading", _fake)


def test_assinante_com_entitlement_recebe_o_horoscopo_de_hoje(client, monkeypatch):
    calls: list[str] = []
    _fake_generate_reading(monkeypatch, calls)
    _register_with_profile(client)
    _grant_plano_lua(client, "lua@example.com")

    response = client.post(f"/api/me/readings/{CONTENT_ID}/generate")
    assert response.status_code == 202, response.text
    assert calls == [CONTENT_ID]

    listed = client.get("/api/me/readings").json()["readings"]
    reading = next(r for r in listed if r["content_id"] == CONTENT_ID)
    assert reading["status"] == "ready"
    assert "Horóscopo de teste" in reading["body_html"]


def test_segunda_chamada_no_mesmo_dia_usa_cache_sem_gerar_de_novo(client, monkeypatch):
    calls: list[str] = []
    _fake_generate_reading(monkeypatch, calls)
    _register_with_profile(client, email="cache@example.com")
    _grant_plano_lua(client, "cache@example.com")

    primeira = client.post(f"/api/me/readings/{CONTENT_ID}/generate")
    assert primeira.status_code == 202
    assert len(calls) == 1

    segunda = client.post(f"/api/me/readings/{CONTENT_ID}/generate")
    assert segunda.status_code == 200, segunda.text
    assert len(calls) == 1, "segunda abertura no mesmo dia não pode gerar de novo"
    assert segunda.json()["reading"]["body_html"] == primeira_body(client)


def primeira_body(client):
    listed = client.get("/api/me/readings").json()["readings"]
    return next(r for r in listed if r["content_id"] == CONTENT_ID)["body_html"]


def test_novo_dia_local_gera_um_horoscopo_novo(client, monkeypatch, db_session):
    calls: list[str] = []
    _fake_generate_reading(monkeypatch, calls)
    _register_with_profile(client, email="novodia@example.com")
    _grant_plano_lua(client, "novodia@example.com")

    primeira = client.post(f"/api/me/readings/{CONTENT_ID}/generate")
    assert primeira.status_code == 202
    assert len(calls) == 1

    # Empurra a leitura salva para "ontem" (no fuso de referência pt-BR/BR:
    # America/Sao_Paulo) para simular a virada de dia sem esperar o relógio.
    reading = db_session.query(Reading).filter(Reading.content_id == CONTENT_ID).one()
    reading.created_at = datetime.now(timezone.utc) - timedelta(days=1, hours=2)
    db_session.add(reading)
    db_session.commit()

    segunda = client.post(f"/api/me/readings/{CONTENT_ID}/generate")
    assert segunda.status_code == 202, segunda.text
    assert len(calls) == 2, "novo dia local precisa gerar um horóscopo novo, não servir o de ontem"


def test_assinante_sem_entitlement_recebe_403_nao_pagina_em_branco(client, monkeypatch):
    calls: list[str] = []
    _fake_generate_reading(monkeypatch, calls)
    _register_with_profile(client, email="semplano@example.com")
    # Sem _grant_plano_lua: conta existe, perfil existe, mas nunca pagou.

    response = client.post(f"/api/me/readings/{CONTENT_ID}/generate")

    assert response.status_code == 403, response.text
    assert isinstance(response.json()["detail"], str)
    assert calls == [], "não pode gerar (nem custar) para quem não tem entitlement"


def test_assinante_sem_perfil_e_convidada_a_completar_dados_nao_erro_generico(client, monkeypatch):
    calls: list[str] = []
    _fake_generate_reading(monkeypatch, calls)
    response = _register(client, "semdados@example.com", "senha-segura", name="Cliente Lua")
    assert response.status_code == 200, response.text
    _grant_plano_lua(client, "semdados@example.com")
    # Nenhum PUT /api/me/profile: a conta nunca informou data/cidade de nascimento.

    response = client.post(f"/api/me/readings/{CONTENT_ID}/generate")

    assert response.status_code == 422, response.text
    assert isinstance(response.json()["detail"], str)
    assert calls == []


def test_cancelada_com_leitura_de_hoje_ja_gerada_perde_acesso_mesmo_assim(client, monkeypatch, db_session):
    """Entitlement vale mais que cache: se venceu, 403 mesmo com Reading pronta."""
    from app.models import Entitlement

    calls: list[str] = []
    _fake_generate_reading(monkeypatch, calls)
    _register_with_profile(client, email="cancelada@example.com")
    _grant_plano_lua(client, "cancelada@example.com")

    gerada = client.post(f"/api/me/readings/{CONTENT_ID}/generate")
    assert gerada.status_code == 202
    assert len(calls) == 1

    entitlement = db_session.query(Entitlement).filter(Entitlement.product_id == "site:plano_lua").one()
    entitlement.status = "revoked"
    db_session.add(entitlement)
    db_session.commit()

    resposta = client.post(f"/api/me/readings/{CONTENT_ID}/generate")
    assert resposta.status_code == 403, resposta.text


def test_virada_de_dia_21h_as_meia_noite_usa_o_dia_da_assinante_nao_do_servidor(monkeypatch, db_session):
    """Mesmo cenário do teste equivalente em horoscope_free: 2026-08-07 01:12 UTC
    é 2026-08-06 22:12 em São Paulo e Buenos Aires. O cache tem que reconhecer
    uma Reading criada "ontem" no relógio da assinante como current, não stale
    só porque UTC já virou o dia."""
    from app import horoscope_free

    momento = datetime(2026, 8, 7, 1, 12, tzinfo=timezone.utc)

    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return momento.astimezone(tz) if tz else momento

    monkeypatch.setattr(horoscope_free, "datetime", _Now)
    monkeypatch.setattr(main_module, "datetime", _Now)

    # Reading criada às 21h30 local (00:30 UTC do mesmo "dia UTC" já rolado
    # para 07/08) — mesmo dia local que ``momento`` (06/08 22:12 local).
    created_at = datetime(2026, 8, 7, 0, 30, tzinfo=timezone.utc)
    reading = Reading(
        user_id="fake-user",
        content_id=CONTENT_ID,
        product_id="site:plano_lua",
        status="ready",
        title="Horóscopo diário",
        input_snapshot={"birth_date": "1990-05-20"},
        body_html="<p>Leitura de ontem à noite, ainda hoje para ela.</p>",
    )
    reading.created_at = created_at

    assert main_module.reading_is_current(reading, CONTENT_ID, {"birth_date": "1990-05-20"}, "pt-BR") is True
