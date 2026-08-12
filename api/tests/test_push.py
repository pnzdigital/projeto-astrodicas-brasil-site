"""Testes do módulo de Web Push (push_notifications.py).

Cobre:
- Registro e remoção de subscription
- Dedup de subscription pelo endpoint_hash
- Não enviar para usuário sem entitlement ativo
- Limpeza de subscription morta (resposta 410)
- Endpoint de chave pública VAPID sem autenticação
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Entitlement, User
from app.push_notifications import (
    PushSubscription,
    _endpoint_hash,
    send_push_to_user,
)
from app.security import create_token, hash_password


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(clean_database):
    from app.main import app
    return TestClient(app)


def _make_user(db, email="push@test.com", locale="pt-BR") -> User:
    user = User(email=email, password_hash=hash_password("senha123"), locale=locale)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _make_entitlement(db, user_id: str, source="site") -> Entitlement:
    ent = Entitlement(
        user_id=user_id,
        product_id="site:diario_astral",
        status="available",
        source=source,
    )
    db.add(ent)
    db.commit()
    return ent


def _auth_cookie(user: User) -> dict:
    token = create_token(user.id)
    return {"site_session": token}


def _fake_sub(n: int = 1) -> dict:
    return {
        "endpoint": f"https://fcm.example.com/push/endpoint-{n}",
        "p256dh": f"p256dh-key-{n}",
        "auth": f"auth-secret-{n}",
        "locale": "pt-BR",
    }


# ---------------------------------------------------------------------------
# Testes de rota
# ---------------------------------------------------------------------------

def test_vapid_public_key_sem_auth(client):
    r = client.get("/api/me/push/vapid-public-key")
    assert r.status_code == 200
    assert "publicKey" in r.json()


def test_subscribe_sem_sessao(client):
    r = client.post("/api/me/push/subscribe", json=_fake_sub())
    assert r.status_code == 401


def test_subscribe_cria_subscription(client):
    db = SessionLocal()
    user = _make_user(db)
    db.close()

    r = client.post(
        "/api/me/push/subscribe",
        json=_fake_sub(),
        cookies=_auth_cookie(user),
    )
    assert r.status_code == 201
    assert r.json()["status"] == "created"

    db = SessionLocal()
    subs = db.query(PushSubscription).filter_by(user_id=user.id).all()
    assert len(subs) == 1
    assert subs[0].endpoint == _fake_sub()["endpoint"]
    db.close()


def test_subscribe_dedup_mesmo_endpoint(client):
    db = SessionLocal()
    user = _make_user(db)
    db.close()

    cookies = _auth_cookie(user)
    client.post("/api/me/push/subscribe", json=_fake_sub(), cookies=cookies)
    r = client.post("/api/me/push/subscribe", json=_fake_sub(), cookies=cookies)
    # Segundo POST no mesmo endpoint → "updated", não duplicado
    assert r.status_code == 201
    assert r.json()["status"] == "updated"

    db = SessionLocal()
    count = db.query(PushSubscription).filter_by(user_id=user.id).count()
    assert count == 1
    db.close()


def test_subscribe_endpoints_diferentes_criam_duas(client):
    db = SessionLocal()
    user = _make_user(db)
    db.close()

    cookies = _auth_cookie(user)
    client.post("/api/me/push/subscribe", json=_fake_sub(1), cookies=cookies)
    client.post("/api/me/push/subscribe", json=_fake_sub(2), cookies=cookies)

    db = SessionLocal()
    count = db.query(PushSubscription).filter_by(user_id=user.id).count()
    assert count == 2
    db.close()


def test_unsubscribe_remove_subscription(client):
    db = SessionLocal()
    user = _make_user(db)
    db.close()

    cookies = _auth_cookie(user)
    client.post("/api/me/push/subscribe", json=_fake_sub(), cookies=cookies)

    r = client.request(
        "DELETE",
        "/api/me/push/unsubscribe",
        json={"endpoint": _fake_sub()["endpoint"]},
        cookies=cookies,
    )
    assert r.status_code == 200

    db = SessionLocal()
    count = db.query(PushSubscription).filter_by(user_id=user.id).count()
    assert count == 0
    db.close()


def test_unsubscribe_endpoint_inexistente_ok(client):
    db = SessionLocal()
    user = _make_user(db)
    db.close()

    r = client.request(
        "DELETE",
        "/api/me/push/unsubscribe",
        json={"endpoint": "https://nao-existe.example.com/push"},
        cookies=_auth_cookie(user),
    )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Testes de lógica de envio
# ---------------------------------------------------------------------------

def test_send_nao_envia_sem_entitlement():
    db = SessionLocal()
    user = _make_user(db)
    # Nenhum entitlement — send_push_to_user deve retornar sem chamar pywebpush
    with patch("app.push_notifications._send_one") as mock_send:
        send_push_to_user(db, user.id, "Título", "Corpo")
        mock_send.assert_not_called()
    db.close()


def test_send_envia_para_entitlement_ativo():
    db = SessionLocal()
    user = _make_user(db)
    _make_entitlement(db, user.id)
    # Adiciona subscription diretamente
    sub = PushSubscription(
        user_id=user.id,
        endpoint="https://fcm.example.com/push/abc",
        endpoint_hash=_endpoint_hash("https://fcm.example.com/push/abc"),
        p256dh="key",
        auth="secret",
    )
    db.add(sub)
    db.commit()

    with patch("app.push_notifications._send_one", return_value=None) as mock_send:
        send_push_to_user(db, user.id, "Título", "Corpo")
        mock_send.assert_called_once()
    db.close()


def test_send_limpa_subscription_morta():
    """Subscription com resposta 410 deve ser deletada automaticamente."""
    db = SessionLocal()
    user = _make_user(db)
    _make_entitlement(db, user.id)

    sub = PushSubscription(
        user_id=user.id,
        endpoint="https://fcm.example.com/push/dead",
        endpoint_hash=_endpoint_hash("https://fcm.example.com/push/dead"),
        p256dh="key",
        auth="secret",
    )
    db.add(sub)
    db.commit()
    sub_id = sub.id

    with patch("app.push_notifications._send_one", return_value="expired"):
        send_push_to_user(db, user.id, "Título", "Corpo")

    # Subscription morta deve ter sido removida
    assert db.get(PushSubscription, sub_id) is None
    db.close()


def test_send_nao_envia_para_entitlement_expirado():
    from datetime import datetime, timedelta, timezone

    db = SessionLocal()
    user = _make_user(db)
    ent = Entitlement(
        user_id=user.id,
        product_id="site:diario_astral",
        status="available",
        source="site",
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db.add(ent)
    db.commit()

    with patch("app.push_notifications._send_one") as mock_send:
        send_push_to_user(db, user.id, "Título", "Corpo")
        mock_send.assert_not_called()
    db.close()
