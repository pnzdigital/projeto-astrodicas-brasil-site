"""Regressão: site_orders pré-existente sem as colunas novas não pode derrubar o checkout.

Bug real: em compose local (Postgres), `site_orders` já existia sem `locale`,
`market`, `customer_email` e com `user_id` NOT NULL. `Base.metadata.create_all`
não altera tabela existente, então o INSERT do checkout falhava com 500. A
correção chama `migrations.ensure_schema()` no startup, que roda os
`ALTER TABLE` idempotentes definidos em `app/migrations.py`.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import migrations
from app.db import Base, engine, get_db
from app.main import app

from conftest import register


def _create_legacy_orders_table(target_engine) -> None:
    """Simula o schema pré-migração numa engine qualquer (site_orders sem
    locale/market/customer_email)."""
    with target_engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS site_orders"))
        connection.execute(
            text(
                """
                CREATE TABLE site_orders (
                    id VARCHAR(36) PRIMARY KEY,
                    user_id VARCHAR(36),
                    provider VARCHAR(40),
                    external_id VARCHAR(255),
                    product_id VARCHAR(120),
                    status VARCHAR(32),
                    amount_minor INTEGER,
                    currency VARCHAR(8),
                    raw_payload TEXT,
                    created_at TIMESTAMP
                )
                """
            )
        )


@pytest.fixture()
def isolated_orders_db(monkeypatch):
    """Banco SQLite dedicado a este teste, isolado do engine global.

    Motivo: este teste dropa/recria `site_orders` com SQL cru. No engine
    global, compartilhado por toda a suíte via `app.db.engine`, isso corrompe
    o schema visto por qualquer outra thread/conexão que esteja em voo no
    mesmo instante (ex.: threads reais de `worker.run_job` disparadas por
    outros testes) — daí o `OperationalError: table site_orders` intermitente
    quando a suíte inteira roda, mesmo o teste passando isolado. Uma engine
    própria elimina a contenção sem enfraquecer a verificação: ainda é uma
    tabela pré-existente sem as colunas novas, ainda é `ensure_schema()`
    rodando os `ALTER TABLE` de verdade contra ela.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="astrodicas-migrations-test-"))
    test_engine = create_engine(
        f"sqlite:///{tmp_dir / 'isolated.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(test_engine)
    _create_legacy_orders_table(test_engine)

    TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    def _override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(migrations, "engine", test_engine)
    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield test_engine
    finally:
        del app.dependency_overrides[get_db]
        test_engine.dispose()


def _drop_and_recreate_as_legacy_schema() -> None:
    """Simula o schema pré-migração: site_orders sem locale/market/customer_email.

    `user_id` fica nullable aqui de propósito: a relaxação de NOT NULL em
    `_relax_order_user_id` só se aplica a PostgreSQL (produção real), então em
    SQLite (usado pelos testes) esse pedaço do schema legado já nasce compatível.
    """
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS site_orders"))
        connection.execute(
            text(
                """
                CREATE TABLE site_orders (
                    id VARCHAR(36) PRIMARY KEY,
                    user_id VARCHAR(36),
                    provider VARCHAR(40),
                    external_id VARCHAR(255),
                    product_id VARCHAR(120),
                    status VARCHAR(32),
                    amount_minor INTEGER,
                    currency VARCHAR(8),
                    raw_payload TEXT,
                    created_at TIMESTAMP
                )
                """
            )
        )


def test_checkout_order_survives_pre_existing_orders_table_missing_new_columns(
    client, monkeypatch, isolated_orders_db
):
    """Reproduz o payload real do portal-demo/checkout.html contra o schema legado."""
    monkeypatch.setenv("CHECKOUT_PROVIDER_BR", "cakto")
    migrations.ensure_schema()

    response = client.post(
        "/api/checkout/order",
        json={
            "product_id": "site:diario_astral_completo",
            "email": "cliente@example.com",
            "name": "Cliente Real",
            "locale": "pt-BR",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["amount_minor"] == 9700
    assert body["currency"] == "BRL"


def test_ensure_schema_is_idempotent(client, monkeypatch):
    monkeypatch.setenv("CHECKOUT_PROVIDER_BR", "cakto")
    _drop_and_recreate_as_legacy_schema()
    migrations.ensure_schema()
    migrations.ensure_schema()  # segunda chamada não deve falhar nem duplicar colunas

    response = client.post(
        "/api/checkout/order",
        json={"product_id": "site:mapa_astral", "email": "outra@example.com", "locale": "pt-BR"},
    )
    assert response.status_code == 200, response.text


def _drop_and_recreate_site_readings_as_legacy_schema() -> None:
    """Simula o schema pré-migração: site_readings sem `source`, `reading_kind`,
    `generated_at`, `input_snapshot` nem `error_message` — colunas presentes no
    modelo (`Reading`, app/models.py) desde o commit inicial, mas nunca
    registradas em `NEW_COLUMNS`. Numa tabela criada antes delas, `create_all`
    nunca as adiciona: todo SELECT em site_readings (inclusive
    GET /api/me/readings, que o painel chama logo após o login) quebra com
    UndefinedColumn."""
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS site_readings"))
        connection.execute(
            text(
                """
                CREATE TABLE site_readings (
                    id VARCHAR(36) PRIMARY KEY,
                    user_id VARCHAR(36),
                    content_id VARCHAR(120),
                    product_id VARCHAR(120),
                    status VARCHAR(32),
                    title VARCHAR(200),
                    body_html TEXT,
                    content_sections JSON,
                    birth_time_assumed BOOLEAN,
                    ascendant_warning JSON,
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP
                )
                """
            )
        )


def test_readings_survive_pre_existing_table_missing_new_columns(client):
    """Reproduz o 500 real: painel loga e imediatamente chama GET
    /api/me/readings, que quebrava com `column site_readings.source does not
    exist` numa base cuja tabela nasceu antes dessas colunas existirem."""
    _drop_and_recreate_site_readings_as_legacy_schema()
    migrations.ensure_schema()

    response = register(client, "leitora@example.com", "senha-forte-123")
    assert response.status_code == 200, response.text

    readings = client.get("/api/me/readings")
    assert readings.status_code == 200, readings.text
