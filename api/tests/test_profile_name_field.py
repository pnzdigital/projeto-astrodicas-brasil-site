"""TDD: PUT /api/me/profile aceita campo name e atualiza User.name se vazio.

(1) name no body + user.name vazio → User.name atualizado.
(2) name no body + user.name preenchido → User.name preservado.
(3) name ausente no body → sem alteração (comportamento anterior preservado).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import User
from conftest import create_user, register


def _register_login(client, email, name):
    r = register(client, email, "senha123", name=name)
    assert r.status_code == 200, r.text


def test_profile_name_preenche_usuario_sem_nome(client, db_session):
    create_user("semnom@pn.com", "senha123", name="")
    client.post("/api/auth/login", json={"email": "semnom@pn.com", "password": "senha123"})
    r = client.put("/api/me/profile", json={
        "name": "Gabriela",
        "birth_date": "1990-01-15",
        "birth_city": "Belo Horizonte",
        "birth_country": "BR",
    })
    assert r.status_code == 200, r.text
    user = db_session.scalar(select(User).where(User.email == "semnom@pn.com"))
    assert user.name == "Gabriela"


def test_profile_name_nao_sobrescreve_nome_existente(client, db_session):
    _register_login(client, "comnom@pn.com", "Isabela")
    r = client.put("/api/me/profile", json={
        "name": "OutroNome",
        "birth_date": "1988-06-20",
        "birth_city": "Curitiba",
        "birth_country": "BR",
    })
    assert r.status_code == 200, r.text
    user = db_session.scalar(select(User).where(User.email == "comnom@pn.com"))
    assert user.name == "Isabela"


def test_profile_sem_name_preserva_comportamento(client, db_session):
    _register_login(client, "normal@pn.com", "Camila")
    r = client.put("/api/me/profile", json={
        "birth_date": "1995-09-10",
        "birth_city": "Porto Alegre",
        "birth_country": "BR",
    })
    assert r.status_code == 200, r.text
    user = db_session.scalar(select(User).where(User.email == "normal@pn.com"))
    assert user.name == "Camila"  # intocado, sem campo name no body
