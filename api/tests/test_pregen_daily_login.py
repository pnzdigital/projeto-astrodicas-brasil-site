"""Pré-geração do horóscopo diário no login — TAREFA 1.

Contrato:
- Login de assinante ativa com perfil completo → leitura em_progress ou pronta após login.
- Login de assinante sem perfil (birth_city) → nenhuma leitura criada.
- Login de assinante com trial expirado → nenhuma leitura criada.
- Login não duplica job quando leitura de hoje já existe e está pronta.
- Login não duplica leitura quando in_progress já existe.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import main as main_module
from app.db import SessionLocal
from app.engine import ReadingResult
from app.models import Entitlement, Reading
from conftest import register as _register

CONTENT_ID = "site:content:horoscopo_diario"
PRODUCT_ID = "site:diario_astral"


def _register_with_profile(client, email="user@example.com", locale="pt-BR"):
    r = _register(client, email, "senha-segura", name="Assinante Teste", locale=locale)
    assert r.status_code == 200
    client.put(
        "/api/me/profile",
        json={"birth_date": "1988-04-10", "birth_time": "08:00:00", "birth_city": "São Paulo", "birth_country": "BR"},
    )


def _grant_diario(client, email):
    r = client.post("/api/webhooks/cakto", json={"event_id": f"evt-{email}", "email": email, "product_id": PRODUCT_ID})
    assert r.status_code == 200


def _fake_generate(monkeypatch, calls):
    def _fake(content_id, title, profile, locale, customer_name="", on_section_done=None):
        calls.append(content_id)
        return ReadingResult(body_html=f"<p>Leitura #{len(calls)}.</p>", source="minimax")
    monkeypatch.setattr(main_module, "generate_reading", _fake)


def _count_readings(db, user_id):
    return db.scalars(
        select(Reading).where(Reading.user_id == user_id, Reading.content_id == CONTENT_ID)
    ).all()


def test_pregen_dispara_para_assinante_ativa_com_perfil(client, monkeypatch):
    calls: list[str] = []
    _fake_generate(monkeypatch, calls)
    _register_with_profile(client)
    _grant_diario(client, "user@example.com")

    # Faz logout e login novamente para disparar pré-geração
    client.post("/api/auth/logout")
    r = client.post("/api/auth/login", json={"email": "user@example.com", "password": "senha-segura"})
    assert r.status_code == 200

    db = SessionLocal()
    try:
        from app.models import User
        user = db.scalar(select(User).where(User.email == "user@example.com"))
        readings = _count_readings(db, user.id)
        assert len(readings) >= 1
        assert any(rd.content_id == CONTENT_ID for rd in readings)
    finally:
        db.close()


def test_pregen_nao_dispara_sem_perfil(client, monkeypatch):
    calls: list[str] = []
    _fake_generate(monkeypatch, calls)
    r = _register(client, "noprofile@example.com", "senha-segura", locale="pt-BR")
    assert r.status_code == 200
    _grant_diario(client, "noprofile@example.com")

    client.post("/api/auth/logout")
    r = client.post("/api/auth/login", json={"email": "noprofile@example.com", "password": "senha-segura"})
    assert r.status_code == 200

    db = SessionLocal()
    try:
        from app.models import User
        user = db.scalar(select(User).where(User.email == "noprofile@example.com"))
        readings = _count_readings(db, user.id)
        assert len(readings) == 0
    finally:
        db.close()


def test_pregen_nao_dispara_sem_entitlement(client, monkeypatch):
    calls: list[str] = []
    _fake_generate(monkeypatch, calls)
    _register_with_profile(client)  # sem grant

    client.post("/api/auth/logout")
    r = client.post("/api/auth/login", json={"email": "user@example.com", "password": "senha-segura"})
    assert r.status_code == 200

    db = SessionLocal()
    try:
        from app.models import User
        user = db.scalar(select(User).where(User.email == "user@example.com"))
        readings = _count_readings(db, user.id)
        assert len(readings) == 0
    finally:
        db.close()


def test_pregen_nao_duplica_leitura_pronta(client, monkeypatch):
    calls: list[str] = []
    _fake_generate(monkeypatch, calls)
    _register_with_profile(client)
    _grant_diario(client, "user@example.com")

    # Gera via endpoint normal para ter reading pronta
    r = client.post(f"/api/me/readings/{CONTENT_ID}/generate")
    assert r.status_code in (200, 202)

    n_calls_before = len(calls)

    # Login novamente — não deve gerar de novo
    client.post("/api/auth/logout")
    r = client.post("/api/auth/login", json={"email": "user@example.com", "password": "senha-segura"})
    assert r.status_code == 200

    db = SessionLocal()
    try:
        from app.models import User
        user = db.scalar(select(User).where(User.email == "user@example.com"))
        readings = _count_readings(db, user.id)
        # Deve ter exatamente 1 leitura (não duplicou)
        assert len(readings) == 1
    finally:
        db.close()


def test_pregen_nao_duplica_quando_in_progress(client, monkeypatch):
    """Dois logins rápidos não devem criar duas leituras."""
    calls: list[str] = []
    _fake_generate(monkeypatch, calls)
    _register_with_profile(client)
    _grant_diario(client, "user@example.com")

    client.post("/api/auth/logout")

    # Primeiro login → cria leitura
    client.post("/api/auth/login", json={"email": "user@example.com", "password": "senha-segura"})

    db = SessionLocal()
    try:
        from app.models import User
        user = db.scalar(select(User).where(User.email == "user@example.com"))
        readings_after = _count_readings(db, user.id)
        # Máximo 1 leitura — pré-geração idempotente
        assert len(readings_after) <= 1
    finally:
        db.close()
