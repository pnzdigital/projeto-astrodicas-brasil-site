"""POST /api/me/readings/{content_id}/generate enfileira um GenerationJob em vez
de rodar a engine inline.

Provas:
1. A rota retorna 202 + in_progress SEM chamar generate_reading diretamente
   (o job é criado no banco; o worker separado o executa depois).
2. run_job() executa generate_reading e atualiza a Reading para ready/fallback.

Os testes usam `skip_auto_run` para desligar o autouse que normalmente roda
jobs sincronamente, permitindo inspecionar o estado "job criado mas não rodado".
"""

from datetime import date

from fastapi import BackgroundTasks, Response
from sqlalchemy import select
from starlette.requests import Request

from app import main
from app.engine import ReadingResult
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


def test_generate_cria_job_sem_chamar_engine_inline(db_session, skip_auto_run):
    """generate() cria GenerationJob no banco e retorna 202 sem bloquear na engine.

    Prova estrutural: o endpoint nunca invoca generate_reading — ele apenas
    enfileira o job. A engine roda no worker separado, em outro processo.
    """
    user_id = _setup_paid_user(db_session, "async-proof@example.com")

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

    # Resposta imediata com in_progress — engine não rodou
    assert response.status_code == 202
    assert result["reading"]["status"] == "in_progress"

    # Job criado no banco com status queued
    job = db_session.scalars(
        select(GenerationJob).where(GenerationJob.user_id == user_id)
    ).first()
    assert job is not None
    assert job.status == "queued"
    assert job.content_id == "site:content:mapa_astral_completo"
    assert job.attempts == 0


def test_run_job_executa_engine_e_atualiza_reading(db_session, monkeypatch):
    """run_job() chama generate_reading e atualiza a Reading para ready."""
    user_id = _setup_paid_user(db_session, "async-job@example.com")

    calls: list[str] = []

    def _fake(content_id, title, profile, locale, customer_name="", on_section_done=None):
        calls.append(content_id)
        return ReadingResult(body_html="<p>Leitura de teste.</p>", source="minimax")

    monkeypatch.setattr(main, "generate_reading", _fake)

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
    reading_id = result["reading"]["id"]

    # Com autouse ativo, run_job já rodou — verifica resultado
    assert calls == ["site:content:mapa_astral_completo"]

    reading = db_session.get(Reading, reading_id)
    db_session.refresh(reading)
    assert reading.status == "ready"
    assert reading.body_html == "<p>Leitura de teste.</p>"
