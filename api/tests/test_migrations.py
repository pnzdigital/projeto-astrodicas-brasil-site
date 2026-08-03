"""Regressão: site_orders pré-existente sem as colunas novas não pode derrubar o checkout.

Bug real: em compose local (Postgres), `site_orders` já existia sem `locale`,
`market`, `customer_email` e com `user_id` NOT NULL. `Base.metadata.create_all`
não altera tabela existente, então o INSERT do checkout falhava com 500. A
correção chama `migrations.ensure_schema()` no startup, que roda os
`ALTER TABLE` idempotentes definidos em `app/migrations.py`.
"""

from __future__ import annotations

from sqlalchemy import text

from app import migrations
from app.db import engine


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


def test_checkout_order_survives_pre_existing_orders_table_missing_new_columns(client):
    """Reproduz o payload real do portal-demo/checkout.html contra o schema legado."""
    _drop_and_recreate_as_legacy_schema()
    migrations.ensure_schema()

    response = client.post(
        "/api/checkout/order",
        json={
            "product_id": "site:oferta_plano_lua_premium",
            "email": "cliente@example.com",
            "name": "Cliente Real",
            "locale": "pt-BR",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["amount_minor"] == 9700
    assert body["currency"] == "BRL"


def test_ensure_schema_is_idempotent(client):
    _drop_and_recreate_as_legacy_schema()
    migrations.ensure_schema()
    migrations.ensure_schema()  # segunda chamada não deve falhar nem duplicar colunas

    response = client.post(
        "/api/checkout/order",
        json={"product_id": "site:mapa_astral", "email": "outra@example.com", "locale": "pt-BR"},
    )
    assert response.status_code == 200, response.text
