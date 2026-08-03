import os
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.admin import router as admin_router
from app.db import SessionLocal
from app.models import Entitlement, Order, User
from app.security import hash_password

os.environ["ADMIN_PASSWORD"] = "letmein-test"


@pytest.fixture()
def admin_client(clean_database):
    app = FastAPI()
    app.include_router(admin_router)
    with TestClient(app) as test_client:
        yield test_client


def seed_orders():
    db = SessionLocal()
    try:
        user = User(email="ana@example.com", password_hash=hash_password("senha-segura"))
        db.add(user)
        db.flush()
        db.add(
            Order(
                id=str(uuid4()),
                user_id=user.id,
                provider="mercadopago",
                external_id="ext-1",
                product_id="site:mapa_astral",
                status="paid",
                amount_minor=4700,
                currency="BRL",
                locale="pt-BR",
                market="BR",
                customer_email=user.email,
            )
        )
        db.add(
            Order(
                id=str(uuid4()),
                user_id=user.id,
                provider="mercadopago",
                external_id="ext-2",
                product_id="site:plano_lua",
                status="paid",
                amount_minor=2790 * 310,
                currency="ARS",
                locale="es-AR",
                market="AR",
                customer_email=user.email,
            )
        )
        db.add(Entitlement(id=str(uuid4()), user_id=user.id, product_id="site:mapa_astral", status="available"))
        db.commit()
    finally:
        db.close()


def test_login_wrong_password(admin_client):
    response = admin_client.post("/api/admin/login", json={"password": "wrong"})
    assert response.status_code == 401


def test_login_without_admin_password_configured_returns_503(admin_client, monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    response = admin_client.post("/api/admin/login", json={"password": "anything"})
    assert response.status_code == 503


def test_sales_requires_auth(admin_client):
    response = admin_client.get("/api/admin/sales")
    assert response.status_code == 401


def test_login_sales_and_summary(admin_client):
    seed_orders()

    login = admin_client.post("/api/admin/login", json={"password": "letmein-test"})
    assert login.status_code == 200, login.text

    session = admin_client.get("/api/admin/session")
    assert session.json() == {"authenticated": True}

    sales = admin_client.get("/api/admin/sales")
    assert sales.status_code == 200, sales.text
    body = sales.json()
    assert body["total"] == 2
    markets = {row["market"] for row in body["sales"]}
    assert markets == {"BR", "AR"}

    filtered = admin_client.get("/api/admin/sales", params={"market": "AR"})
    assert filtered.json()["total"] == 1

    by_status = admin_client.get("/api/admin/sales", params={"status": "paid"})
    assert by_status.json()["total"] == 2

    by_date_range = admin_client.get(
        "/api/admin/sales",
        params={"from_": "2000-01-01T00:00:00", "to": "2999-01-01T00:00:00"},
    )
    assert by_date_range.json()["total"] == 2

    outside_range = admin_client.get(
        "/api/admin/sales",
        params={"from_": "2999-01-01T00:00:00"},
    )
    assert outside_range.json()["total"] == 0

    summary = admin_client.get("/api/admin/summary")
    assert summary.status_code == 200, summary.text
    summary_body = summary.json()
    assert summary_body["revenue_by_market"]["BR"]["currency"] == "BRL"
    assert summary_body["revenue_by_market"]["AR"]["currency"] == "ARS"
    assert summary_body["users_count"] == 1
    assert summary_body["active_entitlements"] == 1

    logout = admin_client.post("/api/admin/logout")
    assert logout.status_code == 200
    after_logout = admin_client.get("/api/admin/session")
    assert after_logout.json() == {"authenticated": False}
