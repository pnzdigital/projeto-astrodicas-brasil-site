"""TDD: leitura gerada 2x pela mesma causa em produção.

Job em execução SAUDÁVEL e longa (>VISIBILITY_TIMEOUT) era reclaimado por um
segundo worker porque locked_at nunca era renovado — a primeira geração,
completa e boa, era descartada em silêncio (ou sobrescrita). Duas correções:

a) heartbeat: worker.run_job renova locked_at enquanto a geração roda, então
   um job saudável não é reclaimado só por demorar.
b) descarte idempotente: se, apesar de tudo, duas gerações da mesma Reading
   correrem em paralelo, quem termina por último percebe que a Reading já
   está 'ready' e descarta o próprio resultado (com log de aviso) em vez de
   sobrescrever.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

import app.worker as worker
from app.db import SessionLocal
from app.models import GenerationJob, Reading, User
from app.security import hash_password


@pytest.fixture()
def db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _make_user_and_reading(db, *, user_id="u-dup", reading_id="r-dup",
                            content_id="site:content:mapa_astral_completo") -> None:
    user = User(id=user_id, email=f"{user_id}@teste.com", password_hash=hash_password("x"), locale="pt-BR")
    db.add(user)
    reading = Reading(id=reading_id, user_id=user_id, content_id=content_id, status="in_progress")
    db.add(reading)
    db.commit()


def _make_job(db, *, user_id="u-dup", reading_id="r-dup",
              content_id="site:content:mapa_astral_completo", status="queued", attempts=0) -> GenerationJob:
    job = GenerationJob(
        reading_id=reading_id,
        content_id=content_id,
        user_id=user_id,
        locale="pt-BR",
        customer_name="Teste",
        status=status,
        attempts=attempts,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


# ---------------------------------------------------------------------------
# a) heartbeat impede reclaim de job saudável e longo
# ---------------------------------------------------------------------------

def test_heartbeat_impede_reclaim_de_job_saudavel_e_longo(db, monkeypatch):
    """Geração mais lenta que o visibility timeout, mas com heartbeat vivo,
    não deve ser reclaimada por um segundo worker."""
    _make_user_and_reading(db)
    job = _make_job(db)

    # Timeout bem curto pra rodar rápido no CI: 0.05min = 3s. Heartbeat a
    # cada 1s (1/3 do timeout, igual à fórmula real do worker.py).
    monkeypatch.setattr(worker, "VISIBILITY_TIMEOUT_MINUTES", 0.05)
    monkeypatch.setattr(worker, "HEARTBEAT_INTERVAL_SECONDS", 1.0)
    monkeypatch.setattr(worker, "_LOCK_ID", "worker-A")

    def _slow_generation(*args, **kwargs):
        # dorme mais que o visibility timeout (3s) — geração longa saudável
        time.sleep(4.5)

    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "app.main._run_generation_job", side_effect=_slow_generation
    ):
        run_thread = threading.Thread(target=worker.run_job, args=(job.id,))
        run_thread.start()

        # Enquanto o job "saudável" roda, um segundo worker tenta reclamar.
        time.sleep(2.0)
        second_worker_db = SessionLocal()
        try:
            reclaimed = worker._claim_next_job(second_worker_db)
        finally:
            second_worker_db.close()

        run_thread.join(timeout=10)

    assert reclaimed is None, "Heartbeat deveria ter renovado locked_at — job não podia ser reclaimado"

    db.refresh(job)
    assert job.status == "done"
    assert job.attempts == 1, "Job não deveria ter sido re-tentado — geração original terminou sozinha"


# ---------------------------------------------------------------------------
# b) descarte idempotente quando duas gerações da mesma Reading terminam
# ---------------------------------------------------------------------------

def test_geracao_atrasada_descarta_a_si_mesma_se_reading_ja_ready(db, monkeypatch, caplog):
    """Simula o caso em que, apesar do heartbeat, um segundo worker já
    concluiu a Reading antes do primeiro terminar de escrever."""
    from app.main import _run_generation_job
    from app import main as main_module

    _make_user_and_reading(db, reading_id="r-race")

    class _Generated:
        source = "llm"
        body_html = "<p>conteudo do worker atrasado</p>"
        sections = []
        birth_time_assumed = False
        ascendant_warning = {}
        warning = ""

    def _fake_generate_reading(*args, **kwargs):
        # Enquanto este worker "gera", outro já concluiu e marcou ready.
        winner_db = SessionLocal()
        try:
            reading = winner_db.get(Reading, "r-race")
            reading.status = "ready"
            reading.body_html = "<p>conteudo do vencedor</p>"
            winner_db.commit()
        finally:
            winner_db.close()
        return _Generated()

    monkeypatch.setattr(main_module, "generate_reading", _fake_generate_reading)
    monkeypatch.setattr(main_module, "sections_for", lambda content_id: [])

    with caplog.at_level("WARNING"):
        _run_generation_job("r-race", "site:content:mapa_astral_completo", "", "u-dup", "pt-BR", "Teste")

    fresh = SessionLocal()
    try:
        reading = fresh.get(Reading, "r-race")
        assert reading.status == "ready"
        assert reading.body_html == "<p>conteudo do vencedor</p>", (
            "Worker atrasado não pode sobrescrever a Reading já concluída pelo vencedor"
        )
    finally:
        fresh.close()

    assert any("descartada por concorrência" in r.message for r in caplog.records), (
        "Descarte por concorrência precisa deixar rastro em log com nível WARNING"
    )
