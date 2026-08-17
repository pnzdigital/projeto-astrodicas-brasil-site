"""Job esgotado no fail_closed não pode deixar a Reading em 'in_progress'
para sempre — antes disso o portal e o admin liam a linha como "ainda
gerando" mesmo com o worker já desistindo de vez (job.status='failed').
Cliente pagou, geração falhou 3x, e ninguém — nem ela, nem o admin — via
que havia um problema. Ver worker._fail_or_retry.
"""

from datetime import date

from app.models import Entitlement, GenerationJob, Profile, Reading
from app.worker import _fail_or_retry
from conftest import create_user


def _setup_job(db_session, email: str) -> tuple[str, GenerationJob]:
    user = create_user(email, "senha-segura-123", name="Cliente Teste")
    db_session.add(Profile(user_id=user.id, birth_date=date(1990, 5, 20), birth_city="Recife", birth_country="BR"))
    db_session.add(Entitlement(user_id=user.id, product_id="site:mapa_astral", status="available", source="test"))
    reading = Reading(
        user_id=user.id,
        content_id="site:content:mapa_astral_completo",
        product_id="site:mapa_astral",
        status="in_progress",
        title="Mapa Astral Completo",
    )
    db_session.add(reading)
    db_session.commit()
    job = GenerationJob(
        reading_id=reading.id,
        content_id="site:content:mapa_astral_completo",
        user_id=user.id,
        locale="es-AR",
        customer_name="Cliente Teste",
        status="running",
        attempts=3,
    )
    db_session.add(job)
    db_session.commit()
    return reading.id, job


def test_job_esgotado_por_fail_closed_marca_reading_como_failed_nao_in_progress(db_session, monkeypatch):
    monkeypatch.setenv("HOROSCOPE_FAIL_CLOSED", "1")
    reading_id, job = _setup_job(db_session, "job-esgotado@example.com")

    _fail_or_retry(db_session, job, "fail_closed: geração retornou fallback editorial")

    reading = db_session.get(Reading, reading_id)
    db_session.refresh(reading)
    assert job.status == "failed"
    assert reading.status == "failed", "portal/admin precisam ver isso como problema, não como 'gerando'"
    assert reading.error_message
