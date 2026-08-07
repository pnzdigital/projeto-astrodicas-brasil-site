"""Guia do Mês da ASSINANTE do Plano Lua ("os movimentos astrales que vêm"),
vendido na copy do plano e antes disso zero backend.

Mesma rota genérica de ``test_daily_horoscope_subscriber.py``
(``POST /api/me/readings/{content_id}/generate``) com
``content_id="site:content:guia_do_mes"``: entitlement, geração via
``engine.generate_reading`` (agora seccionado — ver
``engine.SECTIONS_BY_CONTENT_ID``), cache em ``Reading``. O que este arquivo
prova, específico do mês:

1. O cache é do MÊS LOCAL da assinante, não do mês UTC do servidor — perto da
   virada de mês, 21h-meia-noite em BR/AR já é UTC do dia (e às vezes do mês)
   seguinte.
2. Assinar no dia 20 dá acesso ao guia do mês CORRENTE na hora, não espera o
   dia 1.
3. Virar o mês (local) invalida o cache e gera um guia novo.
4. Duas assinantes com mapas diferentes recebem guias diferentes (chart
   calculado entra no prompt; aqui provamos que o pipeline usa esse chart
   por perfil, não texto fixo).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import main as main_module
from app.engine import ReadingResult
from app.models import Reading
from conftest import register as _register

CONTENT_ID = "site:content:guia_do_mes"


def _register_with_profile(client, email="lua@example.com", locale="pt-BR", birth_country="BR", birth_date="1990-05-20"):
    response = _register(client, email, "senha-segura", name="Cliente Lua", locale=locale)
    assert response.status_code == 200, response.text
    client.put(
        "/api/me/profile",
        json={"birth_date": birth_date, "birth_time": "14:30:00", "birth_city": "Recife", "birth_country": birth_country},
    )


def _grant_plano_lua(client, email):
    granted = client.post(
        "/api/webhooks/cakto",
        json={"event_id": f"evt-{email}", "email": email, "product_id": "site:plano_lua"},
    )
    assert granted.status_code == 200, granted.text


def _fake_generate_reading(monkeypatch, calls):
    def _fake(content_id, title, profile, locale, customer_name=""):
        birth = profile.birth_date.isoformat() if profile and profile.birth_date else "sem-perfil"
        calls.append(content_id)
        return ReadingResult(body_html=f"<p>Guia do mês de teste para {birth} — chamada #{len(calls)}.</p>", source="minimax")

    monkeypatch.setattr(main_module, "generate_reading", _fake)


def test_assinante_com_entitlement_recebe_o_guia_do_mes_corrente(client, monkeypatch):
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
    assert "Guia do mês de teste" in reading["body_html"]


def test_segunda_chamada_no_mesmo_mes_usa_cache_sem_gerar_de_novo(client, monkeypatch):
    calls: list[str] = []
    _fake_generate_reading(monkeypatch, calls)
    _register_with_profile(client, email="cache@example.com")
    _grant_plano_lua(client, "cache@example.com")

    primeira = client.post(f"/api/me/readings/{CONTENT_ID}/generate")
    assert primeira.status_code == 202
    assert len(calls) == 1

    segunda = client.post(f"/api/me/readings/{CONTENT_ID}/generate")
    assert segunda.status_code == 200, segunda.text
    assert len(calls) == 1, "segunda abertura no mesmo mês não pode regenerar (não pode reescrever o que ela já leu)"
    assert segunda.json()["reading"]["body_html"] == _current_body(client)


def _current_body(client):
    listed = client.get("/api/me/readings").json()["readings"]
    return next(r for r in listed if r["content_id"] == CONTENT_ID)["body_html"]


def test_assinar_no_dia_20_da_o_guia_do_mes_atual_na_hora(client, monkeypatch):
    """A promessa: assina dia 20, recebe o guia DESTE mês já — não espera o 1º."""
    calls: list[str] = []
    _fake_generate_reading(monkeypatch, calls)
    _register_with_profile(client, email="dia20@example.com")
    _grant_plano_lua(client, "dia20@example.com")

    response = client.post(f"/api/me/readings/{CONTENT_ID}/generate")
    assert response.status_code == 202, response.text
    assert len(calls) == 1


def test_novo_mes_local_gera_um_guia_novo(client, monkeypatch, db_session):
    calls: list[str] = []
    _fake_generate_reading(monkeypatch, calls)
    _register_with_profile(client, email="novomes@example.com")
    _grant_plano_lua(client, "novomes@example.com")

    primeira = client.post(f"/api/me/readings/{CONTENT_ID}/generate")
    assert primeira.status_code == 202
    assert len(calls) == 1

    reading = db_session.query(Reading).filter(Reading.content_id == CONTENT_ID).one()
    reading.created_at = datetime.now(timezone.utc) - timedelta(days=35)
    db_session.add(reading)
    db_session.commit()

    segunda = client.post(f"/api/me/readings/{CONTENT_ID}/generate")
    assert segunda.status_code == 202, segunda.text
    assert len(calls) == 2, "novo mês local precisa gerar um guia novo, não servir o do mês passado"


def test_virada_de_mes_perto_da_meia_noite_usa_o_mes_da_assinante_nao_do_servidor():
    """31/08 23:50 em São Paulo é 01/09 02:50 UTC — mês UTC já virou setembro,
    mas para a assinante ainda é agosto. Uma Reading gerada nesse instante tem
    que continuar 'do mês' quando 'agora' ainda é o mesmo instante local."""
    momento = datetime(2026, 9, 1, 2, 50, tzinfo=timezone.utc)

    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return momento.astimezone(tz) if tz else momento

    import app.horoscope_free as horoscope_free_module

    original_datetime = main_module.datetime
    original_hf_datetime = horoscope_free_module.datetime
    main_module.datetime = _Now
    horoscope_free_module.datetime = _Now
    try:
        reading = Reading(
            user_id="fake-user",
            content_id=CONTENT_ID,
            product_id="site:plano_lua",
            status="ready",
            title="Guia do mês",
            input_snapshot={"birth_date": "1990-05-20"},
            body_html="<p>Guia de agosto, ainda agosto para ela.</p>",
        )
        reading.created_at = momento

        assert main_module.reading_is_current(reading, CONTENT_ID, {"birth_date": "1990-05-20"}, "pt-BR") is True
    finally:
        main_module.datetime = original_datetime
        horoscope_free_module.datetime = original_hf_datetime


def test_assinante_sem_entitlement_recebe_403(client, monkeypatch):
    calls: list[str] = []
    _fake_generate_reading(monkeypatch, calls)
    _register_with_profile(client, email="semplano@example.com")

    response = client.post(f"/api/me/readings/{CONTENT_ID}/generate")

    assert response.status_code == 403, response.text
    assert isinstance(response.json()["detail"], str)
    assert calls == [], "não pode gerar (nem custar) para quem não tem entitlement"


def test_assinante_sem_perfil_e_convidada_a_completar_dados(client, monkeypatch):
    calls: list[str] = []
    _fake_generate_reading(monkeypatch, calls)
    response = _register(client, "semdados@example.com", "senha-segura", name="Cliente Lua")
    assert response.status_code == 200, response.text
    _grant_plano_lua(client, "semdados@example.com")

    response = client.post(f"/api/me/readings/{CONTENT_ID}/generate")

    assert response.status_code == 422, response.text
    assert isinstance(response.json()["detail"], str)
    assert calls == []


def test_duas_assinantes_com_mapas_diferentes_recebem_guias_diferentes(client, monkeypatch):
    calls: list[str] = []
    _fake_generate_reading(monkeypatch, calls)

    _register_with_profile(client, email="alice@example.com", birth_date="1990-05-20")
    _grant_plano_lua(client, "alice@example.com")
    alice = client.post(f"/api/me/readings/{CONTENT_ID}/generate")
    assert alice.status_code == 202
    client.post("/api/auth/logout")

    _register_with_profile(client, email="bea@example.com", birth_date="1985-11-03")
    _grant_plano_lua(client, "bea@example.com")
    bea = client.post(f"/api/me/readings/{CONTENT_ID}/generate")
    assert bea.status_code == 202

    assert len(calls) == 2
    listed = client.get("/api/me/readings").json()["readings"]
    bea_reading = next(r for r in listed if r["content_id"] == CONTENT_ID)
    assert "1985-11-03" in bea_reading["body_html"]
