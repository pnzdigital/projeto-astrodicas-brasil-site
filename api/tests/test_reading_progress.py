"""Progresso por seção durante geração de leitura.

Cobre:
- sections_total é definido antes da geração e sections_done cresce a cada seção
- reading_to_dict expõe os dois campos (front usa para barra de progresso)
- endpoint /generate retorna 202 com os campos em zero (estado inicial)
- campo sections_total no banco após o job de background
- geração sem seções (content_id sem seções definidas) mantém ambos em zero
"""

import asyncio
from datetime import date

from fastapi import BackgroundTasks, Response
from starlette.requests import Request

from app import main
from app.engine import ReadingResult
from app.models import Entitlement, Profile, Reading
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


def _setup_paid_user(db_session, email: str, product_id: str = "site:mapa_astral") -> str:
    user = create_user(email, "senha-segura-123", name="Cliente Progresso")
    db_session.add(Profile(user_id=user.id, birth_date=date(1990, 5, 20), birth_city="Recife", birth_country="BR"))
    db_session.add(Entitlement(user_id=user.id, product_id=product_id, status="available", source="test"))
    db_session.commit()
    return user.id


def test_generate_sets_sections_total_before_generation(db_session, monkeypatch):
    """sections_total é persistido ANTES de chamar a engine — o front já vê o
    total no primeiro poll, mesmo enquanto sections_done ainda é 0."""
    user_id = _setup_paid_user(db_session, "progress-total@example.com")

    sections_total_at_call_time = []

    def _fake_generate(content_id, title, profile, locale, customer_name, on_section_done=None):
        # Captura sections_total do banco no momento da chamada da engine.
        reading = db_session.query(Reading).filter_by(user_id=user_id, content_id=content_id).first()
        db_session.refresh(reading)
        sections_total_at_call_time.append(reading.sections_total)
        return ReadingResult(body_html="<p>OK</p>", source="minimax")

    monkeypatch.setattr(main, "generate_reading", _fake_generate)

    site_session = main.create_token(user_id, 0)
    background_tasks = BackgroundTasks()
    response = Response()
    main.generate(
        "site:content:mapa_astral_completo",
        _fake_request(),
        response,
        background_tasks,
        site_session=site_session,
        db=db_session,
    )
    asyncio.run(background_tasks())

    # mapa_astral_completo tem 15 seções definidas em SECTIONS_BY_CONTENT_ID
    assert sections_total_at_call_time == [15]


def test_on_section_done_callback_increments_sections_done(db_session, monkeypatch):
    """Cada chamada ao callback incrementa sections_done e persiste no banco.

    O callback é chamado DENTRO da engine (enquanto a sessão ainda está aberta
    no background job) — simulamos isso chamando-o dentro do fake generate, não
    depois do job terminar.
    """
    user_id = _setup_paid_user(db_session, "progress-done@example.com")
    sections_done_snapshots: list[int] = []

    def _fake_generate(content_id, title, profile, locale, customer_name, on_section_done=None):
        # Simula 3 seções concluídas enquanto a sessão do job ainda está aberta.
        if on_section_done:
            for _ in range(3):
                on_section_done()
        return ReadingResult(body_html="<p>OK</p>", source="minimax")

    monkeypatch.setattr(main, "generate_reading", _fake_generate)

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
    asyncio.run(background_tasks())  # roda _run_generation_job com a sessão dele

    # A sessão do job comitou as mudanças; lemos pelo db_session do teste.
    reading = db_session.get(Reading, reading_id)
    db_session.refresh(reading)
    assert reading.sections_done == 3


def test_reading_to_dict_exposes_progress_fields(db_session, monkeypatch):
    """GET /api/me/readings retorna sections_done e sections_total para o front."""
    user_id = _setup_paid_user(db_session, "progress-dict@example.com")

    def _fake_generate(content_id, title, profile, locale, customer_name, on_section_done=None):
        return ReadingResult(body_html="<p>OK</p>", source="minimax")

    monkeypatch.setattr(main, "generate_reading", _fake_generate)

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
    asyncio.run(background_tasks())

    reading = db_session.query(Reading).filter_by(user_id=user_id).first()
    serialized = main.reading_to_dict(reading)
    assert "sections_done" in serialized
    assert "sections_total" in serialized
    assert isinstance(serialized["sections_done"], int)
    assert isinstance(serialized["sections_total"], int)


def test_generate_202_response_includes_progress_fields(db_session, monkeypatch):
    """A resposta inicial de /generate (202) já carrega sections_done=0 e
    sections_total=N, permitindo que o front exiba o total imediatamente."""
    user_id = _setup_paid_user(db_session, "progress-202@example.com")

    monkeypatch.setattr(main, "generate_reading", lambda *a, **kw: ReadingResult(body_html="<p>OK</p>", source="minimax"))

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
    assert result["reading"]["sections_done"] == 0
    assert result["reading"]["sections_total"] == 15  # mapa_astral_completo tem 15


def test_content_without_sections_keeps_counters_zero(db_session, monkeypatch):
    """Content IDs sem seções definidas (ex: horoscopo_diario antes da engine
    retornar) mantêm sections_done e sections_total em 0."""
    user_id = _setup_paid_user(db_session, "progress-nosec@example.com", product_id="site:diario_astral")

    monkeypatch.setattr(main, "generate_reading", lambda *a, **kw: ReadingResult(body_html="<p>OK</p>", source="minimax"))

    site_session = main.create_token(user_id, 0)
    background_tasks = BackgroundTasks()
    response = Response()
    result = main.generate(
        "site:content:horoscopo_diario",
        _fake_request(),
        response,
        background_tasks,
        site_session=site_session,
        db=db_session,
    )
    asyncio.run(background_tasks())
    # horoscopo_diario não tem seções em SECTIONS_BY_CONTENT_ID
    assert result["reading"]["sections_total"] == 0
    assert result["reading"]["sections_done"] == 0
