"""Fecha o buraco entre "pagou" e "recebeu" para cliente nova.

Cliente nasce da compra (checkout.fulfill_order cria o User na hora) — nesse
instante ela nunca tem Profile ainda, então enqueue_map_generation na compra
sempre volta cedo por falta de birth_date/birth_city. Sem gatilho no
salvamento do perfil, a única forma de gerar conteúdo era voltar ao portal e
clicar manualmente no card. Ver main.save_profile e main.enqueue_map_generation.
"""

from datetime import datetime, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Entitlement, GenerationJob, Reading
from conftest import create_user, register


def _grant(user_id: str, product_id: str, source: str = "site") -> None:
    db = SessionLocal()
    try:
        db.add(Entitlement(user_id=user_id, product_id=product_id, status="available", source=source))
        db.commit()
    finally:
        db.close()


def test_compra_sem_perfil_nao_enfileira_nada(client, skip_auto_run):
    resp = register(client, "nova@cliente.com", "senha-segura-123", name="Cliente Nova")
    user_id = resp.json()["user"]["id"]
    _grant(user_id, "site:mapa_astral")

    db = SessionLocal()
    try:
        jobs = db.scalars(select(GenerationJob).where(GenerationJob.user_id == user_id)).all()
        assert jobs == []
    finally:
        db.close()


def test_salvar_perfil_completo_enfileira_mapa_do_produto_comprado(client, skip_auto_run):
    resp = register(client, "nova2@cliente.com", "senha-segura-123", name="Cliente Nova")
    user_id = resp.json()["user"]["id"]
    _grant(user_id, "site:mapa_astral")

    put = client.put("/api/me/profile", json={
        "birth_date": "1990-05-20",
        "birth_city": "Recife",
        "birth_country": "BR",
    })
    assert put.status_code == 200, put.text

    db = SessionLocal()
    try:
        jobs = db.scalars(select(GenerationJob).where(GenerationJob.user_id == user_id)).all()
        assert len(jobs) == 1
        assert jobs[0].content_id == "site:content:mapa_astral_completo"
    finally:
        db.close()


def test_salvar_perfil_de_novo_nao_duplica_enfileiramento(client, skip_auto_run):
    resp = register(client, "nova3@cliente.com", "senha-segura-123", name="Cliente Nova")
    user_id = resp.json()["user"]["id"]
    _grant(user_id, "site:mapa_astral")

    body = {"birth_date": "1990-05-20", "birth_city": "Recife", "birth_country": "BR"}
    client.put("/api/me/profile", json=body)
    client.put("/api/me/profile", json=body)

    db = SessionLocal()
    try:
        jobs = db.scalars(select(GenerationJob).where(GenerationJob.user_id == user_id)).all()
        assert len(jobs) == 1
    finally:
        db.close()


def test_trial_que_salva_perfil_nao_gera_guia_do_mes_nem_previsao_semanal(client, skip_auto_run):
    """Conteúdo periódico (inclusive PAID_ONLY_CONTENT) nunca nasce da compra/perfil —
    isso é papel do run_daily_pregen. Continua fora mesmo para quem paga; para trial
    é ainda mais crítico: não pode vazar Guia do Mês/Previsão Semanal."""
    resp = register(client, "trial@cliente.com", "senha-segura-123", name="Cliente Trial")
    user_id = resp.json()["user"]["id"]
    _grant(user_id, "site:diario_astral", source="trial")

    client.put("/api/me/profile", json={
        "birth_date": "1990-05-20",
        "birth_city": "Recife",
        "birth_country": "BR",
    })

    db = SessionLocal()
    try:
        readings = db.scalars(select(Reading).where(Reading.user_id == user_id)).all()
        content_ids = {r.content_id for r in readings}
        assert "site:content:guia_do_mes" not in content_ids
        assert "site:content:previsao_semanal" not in content_ids
        assert content_ids == set()
    finally:
        db.close()


def test_perfil_incompleto_sem_cidade_nao_enfileira(client, skip_auto_run):
    resp = register(client, "incompleto@cliente.com", "senha-segura-123", name="Cliente Incompleto")
    user_id = resp.json()["user"]["id"]
    _grant(user_id, "site:mapa_astral")

    put = client.put("/api/me/profile", json={"birth_date": "1990-05-20"})
    assert put.status_code == 200, put.text

    db = SessionLocal()
    try:
        jobs = db.scalars(select(GenerationJob).where(GenerationJob.user_id == user_id)).all()
        assert jobs == []
    finally:
        db.close()


def test_falha_no_enfileiramento_nao_impede_salvar_perfil(client, skip_auto_run, monkeypatch):
    from app import main as _app_main

    resp = register(client, "falha@cliente.com", "senha-segura-123", name="Cliente Falha")
    user_id = resp.json()["user"]["id"]
    _grant(user_id, "site:mapa_astral")

    def _boom(db, user, product_ids):
        raise RuntimeError("worker fora do ar")

    monkeypatch.setattr(_app_main, "enqueue_map_generation", _boom)

    put = client.put("/api/me/profile", json={
        "birth_date": "1990-05-20",
        "birth_city": "Recife",
        "birth_country": "BR",
    })
    assert put.status_code == 200, put.text
    assert put.json()["profile"]["birth_city"] == "Recife"
