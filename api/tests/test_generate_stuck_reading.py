"""POST /api/me/readings/{content_id}/generate precisa destravar uma Reading
presa em "in_progress" sem GenerationJob aberto — worker que morreu ou job
que esgotou tentativas fora do caminho fail_closed. Sem isso o card ficava
"gerando leitura…" para sempre porque o endpoint só olhava para
status in ("ready", "fallback") e nunca via a linha travada.

Ver main.STUCK_READING_TIMEOUT_MINUTES e o bloco `stuck = ...` em main.generate().
"""

from datetime import date, datetime, timedelta, timezone

from fastapi import BackgroundTasks, Response
from sqlalchemy import select
from starlette.requests import Request

from app import main
from app.models import Entitlement, GenerationJob, Profile, Reading
from conftest import create_user


def _fake_request() -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/me/readings/x/generate",
        "headers": [],
        "query_string": b"",
    }
    return Request(scope)


def _setup_paid_user(db_session, email: str) -> str:
    user = create_user(email, "senha-segura-123", name="Cliente Teste")
    db_session.add(Profile(user_id=user.id, birth_date=date(1990, 5, 20), birth_city="Recife", birth_country="BR"))
    db_session.add(Entitlement(user_id=user.id, product_id="site:mapa_astral", status="available", source="test"))
    db_session.commit()
    return user.id


def test_reading_presa_velha_sem_job_aberto_e_destravada(db_session, skip_auto_run):
    """Reading in_progress, sem GenerationJob e mais velha que o timeout: nova tentativa."""
    user_id = _setup_paid_user(db_session, "presa-velha@example.com")
    old = datetime.now(timezone.utc) - timedelta(minutes=main.STUCK_READING_TIMEOUT_MINUTES + 5)
    stuck = Reading(
        user_id=user_id,
        content_id="site:content:mapa_astral_completo",
        product_id="site:mapa_astral",
        status="in_progress",
        title="Mapa Astral Completo",
        created_at=old,
    )
    db_session.add(stuck)
    db_session.commit()
    stuck_id = stuck.id

    site_session = main.create_token(user_id, 0)
    response = Response()
    result = main.generate(
        "site:content:mapa_astral_completo",
        _fake_request(),
        response,
        BackgroundTasks(),
        site_session=site_session,
        db=db_session,
    )

    # A Reading velha foi marcada como morta...
    old_reading = db_session.get(Reading, stuck_id)
    db_session.refresh(old_reading)
    assert old_reading.status == "failed"

    # ...e uma nova tentativa foi criada e enfileirada.
    assert response.status_code == 202
    assert result["reading"]["id"] != stuck_id
    assert result["reading"]["status"] == "in_progress"
    job = db_session.scalars(
        select(GenerationJob).where(GenerationJob.reading_id == result["reading"]["id"])
    ).first()
    assert job is not None
    assert job.status == "queued"


def test_reading_presa_recente_ainda_nao_e_destravada(db_session, skip_auto_run):
    """Reading in_progress recente (dentro do timeout), sem job: ainda não é considerada morta."""
    user_id = _setup_paid_user(db_session, "presa-recente@example.com")
    recent = datetime.now(timezone.utc) - timedelta(minutes=1)
    stuck = Reading(
        user_id=user_id,
        content_id="site:content:mapa_astral_completo",
        product_id="site:mapa_astral",
        status="in_progress",
        title="Mapa Astral Completo",
        created_at=recent,
    )
    db_session.add(stuck)
    db_session.commit()
    stuck_id = stuck.id

    site_session = main.create_token(user_id, 0)
    response = Response()
    result = main.generate(
        "site:content:mapa_astral_completo",
        _fake_request(),
        response,
        BackgroundTasks(),
        site_session=site_session,
        db=db_session,
    )

    old_reading = db_session.get(Reading, stuck_id)
    db_session.refresh(old_reading)
    assert old_reading.status == "in_progress"  # não mexeu — ainda dentro do prazo

    # Mesmo assim, como não achou job aberto, cria uma NOVA reading (não bloqueia
    # a cliente enquanto espera o timeout — só não marca a antiga como morta).
    assert result["reading"]["id"] != stuck_id


def test_reading_presa_com_job_ativo_nao_duplica(db_session, skip_auto_run):
    """Reading in_progress com GenerationJob queued/running: devolve a mesma, sem duplicar."""
    user_id = _setup_paid_user(db_session, "presa-com-job@example.com")
    old = datetime.now(timezone.utc) - timedelta(minutes=main.STUCK_READING_TIMEOUT_MINUTES + 5)
    stuck = Reading(
        user_id=user_id,
        content_id="site:content:mapa_astral_completo",
        product_id="site:mapa_astral",
        status="in_progress",
        title="Mapa Astral Completo",
        created_at=old,
    )
    db_session.add(stuck)
    db_session.commit()
    db_session.add(GenerationJob(
        reading_id=stuck.id,
        content_id="site:content:mapa_astral_completo",
        user_id=user_id,
        locale="pt-BR",
        customer_name="Cliente Teste",
        status="running",
    ))
    db_session.commit()
    stuck_id = stuck.id

    site_session = main.create_token(user_id, 0)
    response = Response()
    result = main.generate(
        "site:content:mapa_astral_completo",
        _fake_request(),
        response,
        BackgroundTasks(),
        site_session=site_session,
        db=db_session,
    )

    assert response.status_code == 202
    assert result["reading"]["id"] == stuck_id
    old_reading = db_session.get(Reading, stuck_id)
    db_session.refresh(old_reading)
    assert old_reading.status == "in_progress"  # não foi tocada — job ainda vivo

    jobs = db_session.scalars(
        select(GenerationJob).where(GenerationJob.user_id == user_id)
    ).all()
    assert len(jobs) == 1  # não duplicou
