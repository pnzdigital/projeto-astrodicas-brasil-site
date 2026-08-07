"""POST /api/me/readings/{content_id}/generate não pode mais bloquear a
requisição até o MiniMax terminar (era o motivo do timeout de 60s do nginx
estourar em produção). Este arquivo prova, de forma direta e determinística,
que a rota devolve a resposta ANTES de rodar a geração — sem depender de
timing/sleep, que é frágil em CI.
"""

import asyncio
import time
from datetime import date

from fastapi import BackgroundTasks, Response
from starlette.requests import Request

from app import main
from app.models import Entitlement, Profile
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


def test_generate_endpoint_never_calls_generate_reading_before_returning(db_session, monkeypatch):
    """Prova estrutural (não de timing): chamar a função da rota diretamente
    NUNCA invoca `generate_reading` — ela só é agendada via
    `background_tasks.add_task`, que o FastAPI só executa DEPOIS da resposta
    ser enviada ao cliente. Se a rota voltasse a ser síncrona, este teste
    falharia porque `generate_reading` seria chamada dentro de `main.generate`."""
    user_id = _setup_paid_user(db_session, "async-proof@example.com")

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("generate_reading foi chamada de forma síncrona — rota voltou a bloquear")

    monkeypatch.setattr(main, "generate_reading", _must_not_be_called)

    site_session = main.create_token(user_id, 0)
    background_tasks = BackgroundTasks()
    response = Response()

    started = time.monotonic()
    result = main.generate(
        "site:content:mapa_astral_completo",
        _fake_request(),
        response,
        background_tasks,
        site_session=site_session,
        db=db_session,
    )
    elapsed = time.monotonic() - started

    assert result["reading"]["status"] == "in_progress"
    assert response.status_code == 202
    # Sem chamada de rede nem sleep no caminho síncrono, isto deveria ser
    # near-instant; a asserção de tempo é só um cinto-e-suspensório em cima
    # da prova estrutural acima (generate_reading nunca rodou aqui).
    assert elapsed < 1.0
    assert len(background_tasks.tasks) == 1


def test_background_job_runs_generate_reading_and_updates_the_row(db_session, monkeypatch):
    """A outra metade do contrato: quando o BackgroundTask É executado (como o
    Starlette faz depois de mandar a resposta), a leitura sai de
    'in_progress' para 'ready'/'fallback' de verdade."""
    user_id = _setup_paid_user(db_session, "async-job@example.com")

    calls = []

    def _fake_generate_reading(content_id, title, profile, locale, customer_name):
        calls.append(content_id)
        from app.engine import ReadingResult
        return ReadingResult(body_html="<p>Leitura de teste.</p>", source="minimax")

    monkeypatch.setattr(main, "generate_reading", _fake_generate_reading)

    site_session = main.create_token(user_id, 0)
    background_tasks = BackgroundTasks()
    response = Response()
    result = main.generate(
        "site:content:mapa_astral_completo",
        _fake_request(),
        response,
        background_tasks,
        site_session=site_session,
        db=db_session,
    )
    reading_id = result["reading"]["id"]
    assert calls == []  # ainda não rodou

    asyncio.run(background_tasks())  # simula o que o Starlette faz após enviar a resposta
    assert calls == ["site:content:mapa_astral_completo"]

    from app.models import Reading
    reading = db_session.get(Reading, reading_id)
    assert reading.status == "ready"
    assert reading.body_html == "<p>Leitura de teste.</p>"
