"""TDD: comportamentos de correção da fila durável de geração.

Quatro garantias fundamentais:
a) Dois workers não pegam o mesmo job (SELECT FOR UPDATE SKIP LOCKED, ou
   exclusão por status em SQLite).
b) Job travado em `running` com `locked_at` antigo volta pra `queued` via
   visibility timeout.
c) Job que falha 3× vira `failed` com `last_error` preenchido.
d) Enfileirar o mesmo (user_id, content_id) duas vezes não cria duplicata
   enquanto um job ainda está pendente.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import app.worker as worker
from app.db import SessionLocal
from app.models import GenerationJob


@pytest.fixture()
def db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _make_job(db, *, user_id="u1", content_id="site:content:mapa_astral_completo",
              reading_id="r1", status="queued", attempts=0,
              not_before: datetime | None = None) -> GenerationJob:
    job = GenerationJob(
        reading_id=reading_id,
        content_id=content_id,
        user_id=user_id,
        locale="pt-BR",
        customer_name="Teste",
        status=status,
        attempts=attempts,
    )
    if not_before is not None:
        job.not_before = not_before
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


# ---------------------------------------------------------------------------
# a) Dois workers não pegam o mesmo job
# ---------------------------------------------------------------------------

def test_dois_workers_nao_pegam_o_mesmo_job(db):
    """_claim_next_job deve ser atômica: apenas um dos dois threads reclama o job."""
    _make_job(db)

    claimed_jobs: list[str] = []
    errors: list[Exception] = []

    def _try_claim():
        session = SessionLocal()
        try:
            job = worker._claim_next_job(session)
            if job:
                claimed_jobs.append(job.id)
        except Exception as exc:
            errors.append(exc)
        finally:
            session.close()

    t1 = threading.Thread(target=_try_claim)
    t2 = threading.Thread(target=_try_claim)
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert not errors, f"Erros nos threads: {errors}"
    # Exatamente um dos dois threads deve ter conseguido o job.
    # (Em SQLite, serialização por write-lock garante isso mesmo sem SKIP LOCKED.)
    assert len(claimed_jobs) == 1, (
        f"Esperado 1 claim, obtido {len(claimed_jobs)}: {claimed_jobs}"
    )


# ---------------------------------------------------------------------------
# b) Job travado em `running` volta pra fila via visibility timeout
# ---------------------------------------------------------------------------

def test_job_travado_em_running_volta_pra_queued_pelo_timeout(db):
    """Job com locked_at mais antigo que VISIBILITY_TIMEOUT deve ser reclaimado."""
    old_locked_at = datetime.now(timezone.utc) - timedelta(
        minutes=worker.VISIBILITY_TIMEOUT_MINUTES + 1
    )
    job = _make_job(db, status="running", attempts=1)
    job.locked_at = old_locked_at
    job.locked_by = "worker-morto"
    db.commit()

    session = SessionLocal()
    try:
        claimed = worker._claim_next_job(session)
    finally:
        session.close()

    assert claimed is not None, "Job travado deve ser reclaimado após timeout"
    assert claimed.id == job.id
    assert claimed.status == "running"  # claim inline já pôs running
    assert claimed.attempts == 2        # incrementou do attempt anterior


# ---------------------------------------------------------------------------
# c) Job que falha 3× vira `failed`
# ---------------------------------------------------------------------------

def test_job_falha_3_vezes_e_vira_failed(db, monkeypatch):
    """run_job com _run_generation_job sempre falhando: após 3 tentativas → failed."""
    job = _make_job(db)

    monkeypatch.setattr(
        worker,
        "_LOCK_ID",
        "test-worker",
    )

    call_count = [0]

    def _always_fail(*args, **kwargs):
        call_count[0] += 1
        raise RuntimeError("engine indisponível")

    with patch("app.main._run_generation_job", side_effect=_always_fail):
        # Primeira tentativa
        worker.run_job(job.id)
        db.refresh(job)
        assert job.status == "queued", "Após 1ª falha deve retornar a queued com backoff"
        assert job.attempts == 1

        # Força not_before para o passado para que _claim_next_job o pegue de novo
        job.not_before = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()

        # Segunda tentativa
        worker.run_job(job.id)
        db.refresh(job)
        assert job.status == "queued", "Após 2ª falha deve retornar a queued com backoff"
        assert job.attempts == 2

        job.not_before = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()

        # Terceira tentativa → failed
        worker.run_job(job.id)
        db.refresh(job)

    assert job.status == "failed", "Após 3 falhas deve ser marcado como failed"
    assert job.attempts == worker.MAX_JOB_ATTEMPTS
    assert job.last_error is not None
    assert "engine indisponível" in job.last_error
    assert call_count[0] == worker.MAX_JOB_ATTEMPTS


# ---------------------------------------------------------------------------
# d) Enfileirar o mesmo conteúdo duas vezes não cria duplicata
# ---------------------------------------------------------------------------

def test_enfileirar_mesmo_conteudo_nao_duplica(skip_auto_run):
    """enqueue_generation_job é idempotente por (user_id, content_id) pendente."""
    kwargs = dict(
        reading_id="r-idem",
        content_id="site:content:mapa_astral_completo",
        user_id="u-idem",
        locale="pt-BR",
        customer_name="Idempotência Teste",
    )

    job1 = worker.enqueue_generation_job(**kwargs)
    job2 = worker.enqueue_generation_job(**kwargs)

    assert job1 is not None
    assert job2 is not None
    assert job1.id == job2.id, "Segunda chamada deve retornar o job existente"

    db = SessionLocal()
    try:
        from sqlalchemy import func, select
        count = db.scalar(
            select(func.count()).where(
                GenerationJob.user_id == kwargs["user_id"],
                GenerationJob.content_id == kwargs["content_id"],
            )
        )
    finally:
        db.close()

    assert count == 1, f"Deve existir exatamente 1 job, encontrado {count}"
