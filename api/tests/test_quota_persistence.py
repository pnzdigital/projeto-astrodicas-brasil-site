"""Persistência da cota MiniMax em `site_quota_usage`.

Cobre a razão de existir da tabela: contador em memória zerava a cada
restart de container (Coolify reinicia a cada deploy), então o painel
mostrava zero logo após qualquer deploy mesmo com a semana cheia de uso.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

import app.engine as engine
from app.db import SessionLocal
from app.models import QuotaUsage


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_persistir_uso_cria_linha_e_soma_em_chamadas_seguintes(db):
    engine._persist_quota_usage("MiniMax-M2.7", 120)
    engine._persist_quota_usage("MiniMax-M2.7", 80)

    row = db.query(QuotaUsage).filter(QuotaUsage.model == "MiniMax-M2.7").one()
    assert row.request_count == 2
    assert row.token_count == 200


def test_modelos_diferentes_ficam_em_linhas_separadas_na_mesma_semana(db):
    engine._persist_quota_usage("MiniMax-M2.7", 100)
    engine._persist_quota_usage("MiniMax-M3", 500)

    rows = {r.model: r for r in db.query(QuotaUsage).all()}
    assert rows["MiniMax-M2.7"].request_count == 1
    assert rows["MiniMax-M3"].request_count == 1
    assert rows["MiniMax-M3"].token_count == 500


def test_contagem_sobrevive_a_sessao_nova_simulando_restart(db):
    """Esse é o teste que justifica a mudança inteira: memória de processo
    zera em restart, banco não. Fechar e abrir sessão nova simula o processo
    reiniciando (a conexão antiga morre, uma nova é aberta do zero)."""
    engine._persist_quota_usage("MiniMax-M2.7", 300)
    db.close()

    fresh_session = SessionLocal()
    try:
        row = fresh_session.query(QuotaUsage).filter(QuotaUsage.model == "MiniMax-M2.7").one()
        assert row.request_count == 1
        assert row.token_count == 300
    finally:
        fresh_session.close()


def test_semana_anterior_nao_soma_na_semana_corrente(db, monkeypatch):
    last_week = engine._week_start() - timedelta(days=7)
    db.add(QuotaUsage(week_start=last_week, model="MiniMax-M2.7", request_count=999, token_count=999_000))
    db.commit()

    engine._persist_quota_usage("MiniMax-M2.7", 50)

    current = (
        db.query(QuotaUsage)
        .filter(QuotaUsage.model == "MiniMax-M2.7", QuotaUsage.week_start == engine._week_start())
        .one()
    )
    assert current.request_count == 1
    assert current.token_count == 50

    old = db.query(QuotaUsage).filter(QuotaUsage.week_start == last_week).one()
    assert old.request_count == 999


def test_semana_corrente_e_sempre_segunda_feira():
    tuesday = datetime(2026, 8, 11, 15, 0, tzinfo=timezone.utc)
    monday = datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc)
    assert engine._week_start(tuesday) == date(2026, 8, 10)
    assert engine._week_start(monday) == date(2026, 8, 10)


def test_snapshot_calcula_percentual_do_teto(db, monkeypatch):
    monkeypatch.setenv("MINIMAX_WEEKLY_REQUEST_LIMIT", "1000")
    for _ in range(700):
        engine._persist_quota_usage("MiniMax-M2.7", 10)

    snapshot = engine.get_weekly_quota_snapshot(db)

    assert snapshot["available"] is True
    assert snapshot["limit"] == 1000
    assert snapshot["total_requests"] == 700
    assert snapshot["remaining"] == 300
    assert snapshot["percent_used"] == 70.0
    assert snapshot["by_model"]["MiniMax-M2.7"]["requests"] == 700
    assert snapshot["by_model"]["MiniMax-M2.7"]["tokens"] == 7000


def test_snapshot_usa_default_45000_sem_env(db, monkeypatch):
    monkeypatch.delenv("MINIMAX_WEEKLY_REQUEST_LIMIT", raising=False)
    snapshot = engine.get_weekly_quota_snapshot(db)
    assert snapshot["limit"] == 45000


def test_snapshot_sem_uso_devolve_zero_sem_quebrar(db):
    snapshot = engine.get_weekly_quota_snapshot(db)
    assert snapshot["available"] is True
    assert snapshot["total_requests"] == 0
    assert snapshot["by_model"] == {}


def test_falha_ao_persistir_nao_propaga_excecao(db, monkeypatch):
    """CONSTRAINT dura: erro de métrica nunca pode derrubar a geração."""

    class _BrokenSession:
        def execute(self, *a, **k):
            raise RuntimeError("banco fora do ar")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(engine, "SessionLocal", lambda: _BrokenSession())
    engine._persist_quota_usage("MiniMax-M2.7", 10)  # não deve levantar
