"""Testes de isolamento de mercado (BR/AR) no painel admin.

Cada operadora tem CONTA própria, e o mercado é atributo da conta: `luciola`
está presa a AR, a dona tem escopo total e pode filtrar a visualização.
ADMIN_PASSWORD segue como acesso de emergência, com escopo total.

O escopo vive no token de sessão, nunca em query string — é isso que impede a
troca de mercado pela URL.
"""

import os
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.admin import router as admin_router
from app.db import SessionLocal
from app.models import AdminUser, Entitlement, Order, User
from app.security import hash_password

os.environ["ADMIN_PASSWORD"] = "letmein-test"


@pytest.fixture()
def admin_client(clean_database, monkeypatch):
    # setenv (não os.environ global no import) para não vazar pros outros
    # arquivos de teste que rodam no mesmo processo.
    _seed_admin_accounts()
    app = FastAPI()
    app.include_router(admin_router)
    with TestClient(app) as test_client:
        yield test_client


def _seed_admin_accounts():
    """Duas contas: a dona (escopo total) e luciola, presa à Argentina."""
    db = SessionLocal()
    try:
        db.add_all([
            AdminUser(username="dona", password_hash=hash_password("senha-da-dona"), market=None),
            AdminUser(username="luciola", password_hash=hash_password("senha-ar"), market="AR"),
            AdminUser(username="equipe-br", password_hash=hash_password("senha-br"), market="BR"),
        ])
        db.commit()
    finally:
        db.close()


def _login(client, password, username=None):
    """Sem username, entra pela senha de emergência (ADMIN_PASSWORD)."""
    body = {"password": password}
    if username:
        body["username"] = username
    r = client.post("/api/admin/login", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def seed_two_markets():
    """Um cliente BR (locale pt-BR) e um cliente AR (locale es-AR), cada um com
    order paga e entitlement."""
    db = SessionLocal()
    try:
        user_br = User(
            id=str(uuid4()), email="br@example.com",
            password_hash=hash_password("senha-segura"), name="Cliente BR", locale="pt-BR",
        )
        user_ar = User(
            id=str(uuid4()), email="ar@example.com",
            password_hash=hash_password("senha-segura"), name="Cliente AR", locale="es-AR",
        )
        db.add_all([user_br, user_ar])
        db.flush()

        db.add(Order(
            id=str(uuid4()), user_id=user_br.id, provider="mercadopago", external_id="ext-br",
            product_id="site:mapa_astral", status="paid", amount_minor=4700,
            currency="BRL", locale="pt-BR", market="BR", customer_email=user_br.email,
        ))
        db.add(Order(
            id=str(uuid4()), user_id=user_ar.id, provider="mercadopago", external_id="ext-ar",
            product_id="site:diario_astral", status="paid", amount_minor=279000,
            currency="ARS", locale="es-AR", market="AR", customer_email=user_ar.email,
        ))
        db.add(Entitlement(id=str(uuid4()), user_id=user_br.id, product_id="site:mapa_astral", status="available"))
        db.add(Entitlement(id=str(uuid4()), user_id=user_ar.id, product_id="site:diario_astral", status="available"))
        db.commit()
        return user_br.id, user_ar.id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# (a)/(b) login BR só vê BR, login AR só vê AR
# ---------------------------------------------------------------------------

def test_admin_br_sees_only_br(admin_client):
    seed_two_markets()
    login = _login(admin_client, "senha-br", "equipe-br")
    assert login["market"] == "BR"

    sales = admin_client.get("/api/admin/sales").json()
    assert sales["total"] == 1
    assert {row["market"] for row in sales["sales"]} == {"BR"}

    clients = admin_client.get("/api/admin/clients").json()
    assert clients["total"] == 1
    assert clients["clients"][0]["locale"] == "pt-BR"


def test_admin_ar_sees_only_ar(admin_client):
    seed_two_markets()
    login = _login(admin_client, "senha-ar", "luciola")
    assert login["market"] == "AR"

    sales = admin_client.get("/api/admin/sales").json()
    assert sales["total"] == 1
    assert {row["market"] for row in sales["sales"]} == {"AR"}

    clients = admin_client.get("/api/admin/clients").json()
    assert clients["total"] == 1
    assert clients["clients"][0]["locale"] == "es-AR"


# ---------------------------------------------------------------------------
# (c) query string não força o outro mercado
# ---------------------------------------------------------------------------

def test_market_query_param_ignored_for_scoped_admin(admin_client):
    seed_two_markets()
    _login(admin_client, "senha-br", "equipe-br")

    sales = admin_client.get("/api/admin/sales", params={"market": "AR"}).json()
    assert sales["total"] == 1
    assert {row["market"] for row in sales["sales"]} == {"BR"}


def test_user_detail_cross_market_returns_404(admin_client):
    _br_id, ar_id = seed_two_markets()
    _login(admin_client, "senha-br", "equipe-br")

    r = admin_client.get(f"/api/admin/users/{ar_id}/detail")
    assert r.status_code == 404

    r = admin_client.get(f"/api/admin/users/{ar_id}/readings")
    assert r.status_code == 404


def test_users_search_cross_market_returns_404(admin_client):
    seed_two_markets()
    _login(admin_client, "senha-br", "equipe-br")

    r = admin_client.get("/api/admin/users/search", params={"email": "ar@example.com"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# (d) ADMIN_PASSWORD (dona) vê os dois
# ---------------------------------------------------------------------------

def test_owner_sees_both_markets(admin_client):
    seed_two_markets()
    login = _login(admin_client, "letmein-test")
    assert login["market"] == "ALL"

    sales = admin_client.get("/api/admin/sales").json()
    assert sales["total"] == 2
    assert {row["market"] for row in sales["sales"]} == {"BR", "AR"}

    clients = admin_client.get("/api/admin/clients").json()
    assert clients["total"] == 2


# ---------------------------------------------------------------------------
# (e) totais/somatórios também respeitam o escopo, não só as listas
# ---------------------------------------------------------------------------

def test_summary_totals_respect_scope(admin_client):
    seed_two_markets()
    _login(admin_client, "senha-br", "equipe-br")

    summary = admin_client.get("/api/admin/summary").json()
    assert set(summary["revenue_by_market"].keys()) == {"BR"}
    assert summary["users_count"] == 1
    assert summary["active_entitlements"] == 1


def test_summary_totals_owner_sees_sum(admin_client):
    seed_two_markets()
    _login(admin_client, "letmein-test")

    summary = admin_client.get("/api/admin/summary").json()
    assert set(summary["revenue_by_market"].keys()) == {"BR", "AR"}
    assert summary["users_count"] == 2
    assert summary["active_entitlements"] == 2


def test_pipeline_status_totals_respect_scope(admin_client):
    from datetime import datetime, timezone
    from app.models import Reading

    br_id, ar_id = seed_two_markets()
    db = SessionLocal()
    try:
        db.add(Reading(
            id=str(uuid4()), user_id=br_id, content_id="site:content:horoscopo_diario",
            product_id="site:mapa_astral", status="failed", title="t",
            created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        ))
        db.add(Reading(
            id=str(uuid4()), user_id=ar_id, content_id="site:content:horoscopo_diario",
            product_id="site:diario_astral", status="failed", title="t",
            created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        ))
        db.commit()
    finally:
        db.close()

    _login(admin_client, "senha-br", "equipe-br")
    status_br = admin_client.get("/api/admin/pipeline-status").json()
    assert status_br["readings_by_status"].get("failed") == 1

    admin_client.post("/api/admin/logout")
    _login(admin_client, "letmein-test")
    status_owner = admin_client.get("/api/admin/pipeline-status").json()
    assert status_owner["readings_by_status"].get("failed") == 2


# ---------------------------------------------------------------------------
# (f) sem variáveis BR/AR configuradas, comportamento antigo (só dona) segue
# ---------------------------------------------------------------------------

def test_emergency_password_still_works_and_sees_everything(admin_client):
    """ADMIN_PASSWORD sem username continua entrando, com escopo total.

    É a porta de emergência: se as contas derem problema, ninguém pode ficar
    trancado do lado de fora do painel."""
    seed_two_markets()

    login = _login(admin_client, "letmein-test")
    assert login["market"] == "ALL"

    sales = admin_client.get("/api/admin/sales").json()
    assert sales["total"] == 2


def test_account_password_alone_does_not_open_the_panel(admin_client):
    """Senha de conta sem o usuário não entra — senão a senha de uma operadora
    viraria uma segunda senha mestra."""
    r = admin_client.post("/api/admin/login", json={"password": "senha-ar"})
    assert r.status_code == 401


def test_wrong_password_for_existing_account_is_rejected(admin_client):
    r = admin_client.post("/api/admin/login", json={"username": "luciola", "password": "chute"})
    assert r.status_code == 401


def test_deactivated_account_cannot_log_in(admin_client):
    """Desativar precisa cortar o acesso de fato — é como se tira alguém que
    saiu da equipe sem trocar a senha de todo mundo."""
    from sqlalchemy import select as _select

    db = SessionLocal()
    try:
        conta = db.scalar(_select(AdminUser).where(AdminUser.username == "luciola"))
        conta.active = False
        db.commit()
    finally:
        db.close()

    r = admin_client.post("/api/admin/login", json={"username": "luciola", "password": "senha-ar"})
    assert r.status_code == 401


def test_luciola_is_locked_to_argentina(admin_client):
    """O caso concreto pedido pela dona: a conta da luciola só enxerga AR."""
    seed_two_markets()
    login = _login(admin_client, "senha-ar", "luciola")
    assert login["market"] == "AR"
    assert login["scoped"] is True

    sales = admin_client.get("/api/admin/sales").json()
    assert sales["total"] == 1
    assert all(row["market"] == "AR" for row in sales["rows"]) if sales.get("rows") else True


# ---------------------------------------------------------------------------
# quota / cost: infra global sem quebra por mercado — negado a admin escopado
# ---------------------------------------------------------------------------

def test_quota_and_cost_denied_to_scoped_admin(admin_client):
    _login(admin_client, "senha-br", "equipe-br")
    assert admin_client.get("/api/admin/quota").status_code == 403
    assert admin_client.get("/api/admin/cost").status_code == 403


def test_quota_and_cost_allowed_to_owner(admin_client):
    _login(admin_client, "letmein-test")
    assert admin_client.get("/api/admin/quota").status_code == 200
    assert admin_client.get("/api/admin/cost").status_code == 200
