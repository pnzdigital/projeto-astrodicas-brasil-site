"""Sessão de 7 dias: expiração, renovação por uso e revogação server-side."""

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app import security
from app.db import SessionLocal
from app.models import User


@pytest.fixture()
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _signup(client, email="sessao@example.com", password="senha-bem-comprida"):
    from conftest import register

    return register(client, email, password, name="Sessao", locale="pt-BR")


def _claims(token: str) -> dict:
    return jwt.decode(token, security.SECRET_KEY, algorithms=[security.ALGORITHM])


def test_token_expires_in_seven_days():
    before = datetime.now(timezone.utc)
    claims = _claims(security.create_token("user-1"))
    delta = datetime.fromtimestamp(claims["exp"], tz=timezone.utc) - before
    assert timedelta(days=6, hours=23) < delta <= timedelta(days=7)


def test_token_carries_issued_at():
    """Sem ``iat`` não dá para saber quando renovar."""
    assert "iat" in _claims(security.create_token("user-1"))


def test_cookie_max_age_matches_token_lifetime(client):
    response = _signup(client)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert f"Max-Age={security.SESSION_TTL_SECONDS}" in cookie


def test_expired_token_is_rejected(client, db_session):
    _signup(client)
    user = db_session.query(User).filter(User.email == "sessao@example.com").one()
    stale = jwt.encode(
        {
            "sub": user.id,
            "ep": 0,
            "iat": datetime.now(timezone.utc) - timedelta(days=8),
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        },
        security.SECRET_KEY,
        algorithm=security.ALGORITHM,
    )
    client.cookies.set("site_session", stale)
    assert client.get("/api/me/profile").status_code == 401


def test_session_refreshes_while_in_use(client, db_session):
    """Uso perto do fim da janela renova o cookie: ninguém é deslogado navegando."""
    _signup(client)
    user = db_session.query(User).filter(User.email == "sessao@example.com").one()
    aging = jwt.encode(
        {
            "sub": user.id,
            "ep": 0,
            "iat": datetime.now(timezone.utc) - timedelta(days=5),
            "exp": datetime.now(timezone.utc) + timedelta(days=2),
        },
        security.SECRET_KEY,
        algorithm=security.ALGORITHM,
    )
    client.cookies.set("site_session", aging)
    response = client.get("/api/me/profile")
    assert response.status_code == 200
    assert "set-cookie" in response.headers, "sessão antiga deveria ter sido renovada"
    renewed = _claims(response.cookies["site_session"])
    assert datetime.fromtimestamp(renewed["exp"], tz=timezone.utc) > datetime.now(
        timezone.utc
    ) + timedelta(days=6)


def test_fresh_session_is_not_rewritten_on_every_request(client):
    """Renovar a cada request só gasta banda; a janela é longa o bastante."""
    _signup(client)
    response = client.get("/api/me/profile")
    assert response.status_code == 200
    assert "set-cookie" not in response.headers


def test_logout_revokes_the_token_server_side(client):
    signup = _signup(client)
    token = signup.cookies["site_session"]

    assert client.post("/api/auth/logout").status_code == 200

    # Mesmo reapresentando o cookie roubado, a sessão morreu no servidor.
    client.cookies.set("site_session", token)
    assert client.get("/api/me/profile").status_code == 401


def test_revoke_sessions_kills_every_device(client, db_session):
    signup = _signup(client)
    token = signup.cookies["site_session"]
    user = db_session.query(User).filter(User.email == "sessao@example.com").one()

    security_epoch = user.token_epoch or 0
    from app import sessions

    sessions.revoke_sessions(user)
    db_session.commit()
    assert user.token_epoch == security_epoch + 1

    client.cookies.set("site_session", token)
    assert client.get("/api/me/profile").status_code == 401


def test_password_change_still_drops_old_sessions(client, db_session):
    """Regressão: o epoch do reset de senha continua valendo."""
    signup = _signup(client)
    token = signup.cookies["site_session"]
    user = db_session.query(User).filter(User.email == "sessao@example.com").one()
    user.token_epoch = (user.token_epoch or 0) + 1
    db_session.commit()

    client.cookies.set("site_session", token)
    assert client.get("/api/me/profile").status_code == 401


@pytest.mark.parametrize("attribute", ["HttpOnly", "SameSite=lax"])
def test_cookie_keeps_its_security_attributes(client, attribute):
    cookie = _signup(client).headers["set-cookie"]
    assert attribute in cookie
