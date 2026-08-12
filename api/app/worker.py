"""Worker de geração — processo separado do servidor web.

Iniciar como:  python -m app.worker

Não importa app.main em nível de módulo (evita circular): a importação de
_run_generation_job é feita dentro de run_job() (late import), quando main.py
já está totalmente carregado no processo worker ou nos testes.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .db import SessionLocal, engine
from .models import GenerationJob, Reading

logger = logging.getLogger(__name__)

VISIBILITY_TIMEOUT_MINUTES = int(os.getenv("JOB_VISIBILITY_TIMEOUT_MINUTES", "10"))
POLL_INTERVAL_SECONDS = float(os.getenv("WORKER_POLL_INTERVAL_SECONDS", "2"))
MAX_CONCURRENCY = int(os.getenv("MINIMAX_MAX_CONCURRENCY", "8"))
MAX_JOB_ATTEMPTS = 3
BACKOFF_MINUTES = [1, 5, 15]

_LOCK_ID = socket.gethostname()
_stop_event = threading.Event()


def _handle_signal(signum, frame) -> None:  # pragma: no cover
    logger.info("Sinal %s: parando de puxar jobs novos.", signum)
    _stop_event.set()


# Só a thread principal pode registrar handler de sinal. Se este módulo for
# importado de dentro de uma thread (aconteceu quando o worker rodava como
# `python -m app.worker`: o módulo virava __main__ e o import tardio de main.py
# carregava uma SEGUNDA cópia como app.worker, já fora da thread principal),
# registrar aqui levantaria ValueError e derrubaria o job.
try:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
except ValueError:  # pragma: no cover — import fora da thread principal
    logger.debug("Handlers de sinal não registrados: import fora da thread principal.")


# ---------------------------------------------------------------------------
# Enqueue
# ---------------------------------------------------------------------------

def enqueue_generation_job(
    reading_id: str,
    content_id: str,
    user_id: str,
    locale: str,
    customer_name: str,
) -> GenerationJob | None:
    """Enfileira job de geração. Idempotente por (user_id, content_id) em aberto.

    Abre sessão própria para não expirar objetos da sessão do chamador
    (expire_on_commit=True do SQLAlchemy expiraria a Reading da requisição web).
    """
    db = SessionLocal()
    try:
        existing = db.scalars(
            select(GenerationJob).where(
                GenerationJob.user_id == user_id,
                GenerationJob.content_id == content_id,
                GenerationJob.status.in_(["queued", "running"]),
            )
        ).first()
        if existing:
            return existing
        job = GenerationJob(
            reading_id=reading_id,
            content_id=content_id,
            user_id=user_id,
            locale=locale,
            customer_name=customer_name,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return job
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Claim (worker loop)
# ---------------------------------------------------------------------------

def _claim_next_job(db: Session) -> GenerationJob | None:
    """Pega o próximo job disponível atomicamente e o marca como running.

    Em Postgres usa SELECT FOR UPDATE SKIP LOCKED para garantir que dois
    workers nunca peguem o mesmo job. Em SQLite (dev/testes), usa select
    simples — o banco serializa writes e a exclusão por status=running é
    suficiente para uso com único processo.

    Também recupera jobs travados (running com locked_at mais velho que
    VISIBILITY_TIMEOUT_MINUTES) causados por crash ou deploy sem graceful.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=VISIBILITY_TIMEOUT_MINUTES)

    if engine.dialect.name == "postgresql":
        row = db.execute(
            text("""
                SELECT id FROM generation_jobs
                WHERE (status = 'queued' AND not_before <= :now)
                   OR (status = 'running' AND locked_at < :cutoff)
                ORDER BY created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            """),
            {"now": now, "cutoff": cutoff},
        ).first()
        if not row:
            return None
        job = db.get(GenerationJob, row[0])
    else:
        # SQLite: SELECT candidate, then UPDATE with status guard (optimistic lock).
        # Two concurrent sessions can both SELECT the same row, but only one will
        # win the subsequent UPDATE because SQLite serializes writes; the loser
        # gets rowcount=0 and returns None.
        candidate = db.scalars(
            select(GenerationJob)
            .where(
                ((GenerationJob.status == "queued") & (GenerationJob.not_before <= now))
                | ((GenerationJob.status == "running") & (GenerationJob.locked_at < cutoff))
            )
            .order_by(GenerationJob.created_at)
            .limit(1)
        ).first()
        if not candidate:
            return None
        result = db.execute(
            text("""
                UPDATE generation_jobs
                SET status = 'running',
                    locked_at = :now,
                    locked_by = :worker,
                    attempts = attempts + 1
                WHERE id = :id
                  AND (
                      (status = 'queued' AND not_before <= :now)
                      OR (status = 'running' AND locked_at < :cutoff)
                  )
            """),
            {"now": now, "worker": _LOCK_ID, "id": candidate.id, "cutoff": cutoff},
        )
        db.commit()
        if result.rowcount == 0:
            return None  # another worker won the optimistic race
        db.refresh(candidate)
        return candidate

    if not job:
        return None

    job.status = "running"
    job.locked_at = now
    job.locked_by = _LOCK_ID
    job.attempts += 1
    db.commit()
    return job


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

def run_job(job_id: str) -> None:
    """Executa um job de geração e atualiza seu status.

    Pode ser chamado diretamente (testes, autouse) sobre um job queued, ou
    pelo worker_loop sobre um job já claimed (running). Incrementa attempts
    antes de rodar; chama _fail_or_retry em caso de exceção.

    Late import de _run_generation_job para evitar circular em nível de módulo:
    main.py importa worker.py; worker.py só importa main.py dentro desta função.
    """
    from .main import _run_generation_job  # late import — main já carregado

    db = SessionLocal()
    try:
        job = db.get(GenerationJob, job_id)
        if not job or job.status in ("done", "failed"):
            return

        # Claim inline se o job ainda está queued (chamada direta, não via loop)
        if job.status == "queued":
            job.attempts += 1
            job.status = "running"
            job.locked_at = datetime.now(timezone.utc)
            job.locked_by = _LOCK_ID
            db.commit()

        reading = db.get(Reading, job.reading_id)
        title = reading.title if reading else ""

        try:
            _run_generation_job(
                job.reading_id,
                job.content_id,
                title,
                job.user_id,
                job.locale,
                job.customer_name,
            )
        except Exception as exc:
            logger.exception("Job %s: _run_generation_job levantou exceção", job_id)
            db.refresh(job)
            _fail_or_retry(db, job, str(exc))
            return

        db.refresh(job)
        job.status = "done"
        db.commit()

        # Notificação push: dispara fora da sessão do job para não travar o loop
        try:
            from .push_notifications import notify_reading_done  # late import
            reading_title = reading.title if reading else ""
            notify_reading_done(job.reading_id, job.user_id, job.locale, reading_title)
        except Exception:
            logger.exception("Falha ao enviar push para job %s (não bloqueia o worker)", job_id)

    except Exception:
        logger.exception("Erro inesperado no job %s", job_id)
    finally:
        db.close()


def _fail_or_retry(db: Session, job: GenerationJob, error: str) -> None:
    """Decide entre retry com backoff ou marcar job como failed."""
    job.last_error = error[:2000]
    if job.attempts >= MAX_JOB_ATTEMPTS:
        job.status = "failed"
        reading = db.get(Reading, job.reading_id)
        if reading and reading.status not in ("ready",):
            reading.status = "fallback"
            reading.error_message = "Leitura temporariamente indisponível. Tente novamente mais tarde."
    else:
        job.status = "queued"
        idx = min(job.attempts - 1, len(BACKOFF_MINUTES) - 1)
        job.not_before = datetime.now(timezone.utc) + timedelta(minutes=BACKOFF_MINUTES[idx])
        job.locked_at = None
        job.locked_by = None
    db.commit()


# ---------------------------------------------------------------------------
# Worker loop
# ---------------------------------------------------------------------------

def worker_loop() -> None:  # pragma: no cover
    """Loop principal do worker — roda até SIGTERM."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger.info(
        "Worker iniciado. MAX_CONCURRENCY=%d VISIBILITY_TIMEOUT=%dmin",
        MAX_CONCURRENCY,
        VISIBILITY_TIMEOUT_MINUTES,
    )

    from .db import Base
    from . import migrations

    Base.metadata.create_all(engine)
    migrations.ensure_schema()

    active: list[threading.Thread] = []

    while not _stop_event.is_set():
        active = [t for t in active if t.is_alive()]

        if len(active) < MAX_CONCURRENCY:
            db = SessionLocal()
            try:
                job = _claim_next_job(db)
                # Lê o id DENTRO da sessão: depois do close() o objeto está
                # destacado e qualquer atributo levanta DetachedInstanceError,
                # derrubando o worker em loop (aconteceu em produção).
                job_id = job.id if job else None
            finally:
                db.close()

            if job_id:
                t = threading.Thread(
                    target=run_job,
                    args=(job_id,),
                    daemon=True,
                    name=f"job-{job_id[:8]}",
                )
                t.start()
                active.append(t)
                continue  # poll imediatamente por mais jobs

        _stop_event.wait(timeout=POLL_INTERVAL_SECONDS)

    logger.info("SIGTERM: aguardando %d jobs terminarem…", len(active))
    for t in active:
        t.join(timeout=300)
    logger.info("Worker encerrado.")


if __name__ == "__main__":
    worker_loop()  # pragma: no cover
