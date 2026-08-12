"""TDD: trial propaga dados de nascimento e nome para o usuário.

Casos obrigatórios:
(1) Novo usuário com dados de nascimento → Profile criado após trial.
(2) Novo usuário com nome → User.name salvo.
(3) Usuário existente sem nome → nome atualizado a partir do trial body.
(4) Usuário existente COM nome → nome NÃO sobrescrito (vazio nunca substitui preenchido).
(5) Dados de nascimento inválidos/incompletos → trial NÃO falha, profile fica incompleto.
(6) Usuário existente sem profile + dados válidos → Profile criado.
(7) Usuário existente com profile → profile NÃO sobrescrito pelo trial.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Profile, User
from conftest import create_user


def _trial(client, email, name="", birth_date=None, birth_time=None,
           birth_city=None, birth_state=None, birth_country=None, locale="pt-BR"):
    payload = {"email": email, "name": name, "locale": locale}
    if birth_date is not None:
        payload["birth_date"] = birth_date
    if birth_time is not None:
        payload["birth_time"] = birth_time
    if birth_city is not None:
        payload["birth_city"] = birth_city
    if birth_state is not None:
        payload["birth_state"] = birth_state
    if birth_country is not None:
        payload["birth_country"] = birth_country
    return client.post("/api/trial/start", json=payload)


# (1) Novo usuário com dados completos de nascimento → Profile criado
def test_trial_cria_profile_para_usuario_novo(client, db_session):
    r = _trial(client, "nova@test.com", name="Maria", birth_date="1990-05-20",
               birth_time="14:30", birth_city="Recife", birth_state="PE")
    assert r.status_code == 200, r.text
    user = db_session.scalar(select(User).where(User.email == "nova@test.com"))
    assert user is not None
    profile = db_session.get(Profile, user.id)
    assert profile is not None
    assert str(profile.birth_date) == "1990-05-20"
    assert profile.birth_city == "Recife"
    assert profile.birth_state == "PE"


# (2) Nome de novo usuário salvo
def test_trial_salva_nome_usuario_novo(client, db_session):
    r = _trial(client, "nome@test.com", name="Juliana Silva")
    assert r.status_code == 200, r.text
    user = db_session.scalar(select(User).where(User.email == "nome@test.com"))
    assert user.name == "Juliana Silva"


# (3) Usuário existente sem nome → nome preenchido
def test_trial_preenche_nome_se_usuario_sem_nome(client, db_session):
    create_user("semnom@test.com", "senha123", name="")
    r = _trial(client, "semnom@test.com", name="Letícia")
    # Usuário já existe mas sem trial anterior — deve ativar trial
    assert r.status_code == 200, r.text
    user = db_session.scalar(select(User).where(User.email == "semnom@test.com"))
    assert user.name == "Letícia"


# (4) Usuário existente COM nome → nome preservado
def test_trial_nao_sobrescreve_nome_existente(client, db_session):
    create_user("comnom@test.com", "senha123", name="Ana Paula")
    r = _trial(client, "comnom@test.com", name="OutroNome")
    assert r.status_code == 200, r.text
    user = db_session.scalar(select(User).where(User.email == "comnom@test.com"))
    assert user.name == "Ana Paula"  # não alterado


# (5) Dados de nascimento inválidos → trial não falha
def test_trial_nao_falha_com_nascimento_invalido(client, db_session):
    r = _trial(client, "invalido@test.com", name="Teste", birth_date="invalido")
    assert r.status_code == 200, r.text  # trial ativado mesmo com dado ruim


# (5b) Nascimento parcial (só cidade, sem data) → trial não falha
def test_trial_nao_falha_com_nascimento_parcial(client, db_session):
    r = _trial(client, "parcial@test.com", name="Teste", birth_city="São Paulo")
    assert r.status_code == 200, r.text


# (6) Usuário existente sem profile → Profile criado no trial
def test_trial_cria_profile_usuario_existente_sem_profile(client, db_session):
    create_user("exstprof@test.com", "senha123", name="Carla")
    r = _trial(client, "exstprof@test.com", name="Carla",
               birth_date="1985-03-10", birth_city="Salvador", birth_state="BA")
    assert r.status_code == 200, r.text
    user = db_session.scalar(select(User).where(User.email == "exstprof@test.com"))
    profile = db_session.get(Profile, user.id)
    assert profile is not None
    assert profile.birth_city == "Salvador"


# (7) Usuário existente COM profile → profile não sobrescrito
def test_trial_nao_sobrescreve_profile_existente(client, db_session):
    user = create_user("compf@test.com", "senha123", name="Diana")
    db = SessionLocal()
    try:
        db.add(Profile(user_id=user.id, birth_city="Fortaleza", birth_state="CE"))
        db.commit()
    finally:
        db.close()
    r = _trial(client, "compf@test.com", name="Diana",
               birth_date="1992-07-15", birth_city="Natal")
    assert r.status_code == 200, r.text
    profile = db_session.get(Profile, user.id)
    assert profile.birth_city == "Fortaleza"  # intocado
