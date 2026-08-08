"""Migrações leves de schema para bancos já criados em produção.

`Base.metadata.create_all` cria tabelas novas, mas nunca adiciona colunas a
uma tabela existente. Como o site já roda no Coolify com dados reais, as
colunas novas de `site_orders` são aplicadas aqui, de forma idempotente e
compatível com SQLite e PostgreSQL.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text

from .db import engine

logger = logging.getLogger(__name__)

# tabela -> (coluna, tipo SQL, default)
NEW_COLUMNS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "site_profiles": (
        ("birth_state", "VARCHAR(64)", ""),
        ("partner_birth_state", "VARCHAR(64)", ""),
    ),
    "site_orders": (
        ("locale", "VARCHAR(10)", "'pt-BR'"),
        ("market", "VARCHAR(2)", "'BR'"),
        ("customer_email", "VARCHAR(320)", "''"),
        ("updated_at", "TIMESTAMP", ""),
    ),
    "site_users": (
        ("token_epoch", "INTEGER", "0"),
    ),
    "site_readings": (
        # JSON no Postgres; SQLite mapeia sqlalchemy.JSON para TEXT internamente,
        # mas ADD COLUMN com tipo "JSON" funciona nos dois (SQLite aceita
        # qualquer nome de tipo por afinidade). Leituras pré-existentes ficam
        # com NULL aqui; reading_to_dict() trata isso como lista vazia.
        ("content_sections", "JSON", ""),
        # Aviso de hora assumida: sem essas colunas o flag calculado em
        # engine.generate_reading morria ao fim da requisição de /generate e
        # a leitura persistida (e o PDF gerado depois) não sabiam que o
        # Ascendente era estimativa.
        ("birth_time_assumed", "BOOLEAN", "FALSE"),
        ("ascendant_warning", "JSON", ""),
        # Colunas que já existiam no modelo (Reading, app/models.py) desde o
        # commit inicial, mas nunca entraram nesta lista: numa tabela criada
        # antes delas existirem, `create_all` nunca as adiciona e todo SELECT
        # em site_readings — inclusive GET /api/me/readings — quebra com
        # UndefinedColumn. Isso derrubava o painel: refreshPrivateData() faz
        # Promise.all incluindo /api/me/readings, então até um login com
        # senha correta era reportado como falha e o reload perdia a sessão.
        ("source", "VARCHAR(16)", "'llm'"),
        ("reading_kind", "VARCHAR(32)", "'love'"),
        ("generated_at", "VARCHAR(40)", "''"),
        ("input_snapshot", "JSON", ""),
        ("error_message", "TEXT", "''"),
        # Progresso por seção — exposto para o front exibir barra real durante geração.
        ("sections_done", "INTEGER", "0"),
        ("sections_total", "INTEGER", "0"),
    ),
}


def ensure_schema() -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    for table, columns in NEW_COLUMNS.items():
        if table not in existing_tables:
            continue
        present = {column["name"] for column in inspector.get_columns(table)}
        for name, sql_type, default in columns:
            if name in present:
                continue
            clause = f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"
            if default:
                clause += f" DEFAULT {default}"
            try:
                with engine.begin() as connection:
                    connection.execute(text(clause))
                logger.info("Coluna %s.%s criada.", table, name)
            except Exception as exc:  # pragma: no cover - depende do banco real
                logger.warning("Não foi possível criar %s.%s: %s", table, name, exc)

    _relax_order_user_id(inspector, existing_tables)
    _rename_product_ids(existing_tables)
    _ensure_unique_indices(existing_tables)


# Renomeação dos product_id internos: plano_lua → diario_astral e variantes.
# Aplicada em dev (onde há dados) e em prod quando a primeira compra aparecer.
_PRODUCT_ID_RENAMES: list[tuple[str, str]] = [
    ("site:oferta_plano_lua_premium_bump", "site:diario_astral_completo_bump"),
    ("site:oferta_plano_lua_premium",      "site:diario_astral_completo"),
    ("site:oferta_plano_lua_exit",         "site:diario_astral_oferta_saida"),
    ("site:combo_plano_lua_mapa_astral",   "site:combo_diario_astral_mapa_astral"),
    ("site:combo_plano_lua_mapa_amor",     "site:combo_diario_astral_mapa_amor"),
    ("site:combo_plano_lua_mapa_prosperidade", "site:combo_diario_astral_mapa_prosperidade"),
    ("site:plano_lua",                     "site:diario_astral"),
]

_PRODUCT_ID_TABLES = ("site_orders", "site_entitlements", "site_subscriptions")


def _rename_product_ids(existing_tables: set[str]) -> None:
    for table in _PRODUCT_ID_TABLES:
        if table not in existing_tables:
            continue
        for old_id, new_id in _PRODUCT_ID_RENAMES:
            try:
                with engine.begin() as connection:
                    connection.execute(
                        text(f"UPDATE {table} SET product_id = :new WHERE product_id = :old"),
                        {"new": new_id, "old": old_id},
                    )
            except Exception as exc:  # pragma: no cover
                logger.warning("Rename product_id %s→%s em %s: %s", old_id, new_id, table, exc)


_NEW_INDICES: list[tuple[str, str, list[str]]] = [
    # Garante 1 subscription por (user, produto) — impede race condition em start_trial.
    ("site_subscriptions", "uq_site_subscription_user_product", ["user_id", "product_id"]),
]


def _ensure_unique_indices(existing_tables: set[str]) -> None:
    for table, index_name, columns in _NEW_INDICES:
        if table not in existing_tables:
            continue
        cols = ", ".join(columns)
        sql = f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table} ({cols})"
        try:
            with engine.begin() as connection:
                connection.execute(text(sql))
            logger.info("Índice único %s criado em %s.", index_name, table)
        except Exception as exc:
            logger.warning("Não foi possível criar índice %s em %s: %s", index_name, table, exc)


def _relax_order_user_id(inspector, existing_tables: set[str]) -> None:
    """Ordens pendentes existem antes da conta: `user_id` precisa aceitar nulo."""
    if "site_orders" not in existing_tables or engine.dialect.name != "postgresql":
        return
    try:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE site_orders ALTER COLUMN user_id DROP NOT NULL"))
    except Exception as exc:  # pragma: no cover - já pode estar nulável
        logger.debug("user_id de site_orders já aceita nulo: %s", exc)
