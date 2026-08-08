"""Acesso com prazo tem que acabar de verdade.

A coluna ``expires_at`` existia sem ninguém ler: nenhum produto vencia. Com o
Diário Astral virando assinatura com 3 dias grátis, um entitlement vencido que
continua liberando conteúdo é o pior bug possível do funil — a pessoa cancela,
para de pagar e continua recebendo horóscopo diário.

O outro lado importa igual: compra avulsa não tem prazo e não pode ser afetada.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import entitlements
from app.models import Entitlement, Profile, User
from app.security import hash_password


AGORA = datetime.now(timezone.utc)


def _usuario(db, **entitlement_kwargs) -> User:
    user = User(email="cliente@astrodicas.com", password_hash=hash_password("senha-de-teste"), locale="pt-BR")
    db.add(user)
    db.flush()
    db.add(Profile(user_id=user.id, birth_date=datetime(1990, 7, 15).date(), birth_city="São Paulo"))
    db.add(Entitlement(user_id=user.id, product_id="site:diario_astral", status="available", **entitlement_kwargs))
    db.commit()
    return user


def test_compra_avulsa_nao_vence(db_session):
    user = _usuario(db_session)
    concedido = db_session.scalar(select(Entitlement).where(Entitlement.user_id == user.id))

    assert concedido.expires_at is None
    assert entitlements.is_active(concedido)
    assert entitlements.active(db_session, user.id, "site:diario_astral")


def test_entitlement_vencido_perde_o_acesso(db_session):
    user = _usuario(db_session, expires_at=AGORA - timedelta(minutes=1))

    assert entitlements.active(db_session, user.id, "site:diario_astral") is None


def test_entitlement_dentro_do_prazo_mantem_o_acesso(db_session):
    user = _usuario(db_session, expires_at=AGORA + timedelta(days=3))

    assert entitlements.active(db_session, user.id, "site:diario_astral")


def test_expires_at_ingenuo_e_tratado_como_utc(db_session):
    """SQLite devolve datetime sem tzinfo; comparar direto levantaria TypeError."""
    ingenuo = Entitlement(
        user_id="qualquer",
        product_id="site:diario_astral",
        status="available",
        expires_at=(AGORA + timedelta(days=1)).replace(tzinfo=None),
    )

    assert entitlements.is_active(ingenuo) is True


def test_entitlement_revogado_nao_vale_mesmo_sem_prazo():
    revogado = Entitlement(user_id="qualquer", product_id="site:diario_astral", status="revoked")

    assert entitlements.is_active(revogado) is False


def test_o_gate_da_leitura_recusa_entitlement_vencido(client):
    """O teste que importa: a rota que entrega o conteúdo tem que negar."""
    from conftest import register
    from app.db import SessionLocal

    register(client, "vencido@astrodicas.com", "senha-de-teste-123")
    client.put(
        "/api/me/profile",
        json={"birth_date": "1990-07-15", "birth_city": "São Paulo", "birth_country": "BR"},
    )

    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == "vencido@astrodicas.com"))
        db.add(
            Entitlement(
                user_id=user.id,
                product_id="site:diario_astral",
                status="available",
                expires_at=AGORA - timedelta(days=1),
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.post("/api/me/readings/site:content:horoscopo_diario/generate")

    assert response.status_code == 403, response.text
