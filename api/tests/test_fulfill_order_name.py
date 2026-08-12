"""TDD: fulfill_order propaga nome para usuário existente sem nome.

(1) Usuário existente sem nome → nome do pedido gravado.
(2) Usuário existente COM nome → nome não sobrescrito.
(3) Novo usuário criado pelo fulfill_order → nome do pedido gravado.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.checkout import fulfill_order
from app.db import SessionLocal
from app.models import Order, User
from app.security import hash_password
from conftest import create_user


def _make_order(db, email, name, product_id="site:mapa_astral"):
    order = Order(
        provider="cakto",
        external_id=f"ext-{email}",
        product_id=product_id,
        status="paid",
        amount_minor=3490,
        currency="BRL",
        locale="pt-BR",
        market="BR",
        customer_email=email,
        raw_payload={"name": name},
    )
    db.add(order)
    db.flush()
    return order


def test_fulfill_order_preenche_nome_de_usuario_sem_nome():
    db = SessionLocal()
    try:
        user = User(email="semnom@ff.com", password_hash=hash_password("x"), name="")
        db.add(user)
        db.flush()
        order = _make_order(db, "semnom@ff.com", "Renata")
        fulfill_order(db, order)
        db.refresh(user)
        assert user.name == "Renata"
    finally:
        db.rollback()
        db.close()


def test_fulfill_order_nao_sobrescreve_nome_existente():
    db = SessionLocal()
    try:
        user = User(email="comnom@ff.com", password_hash=hash_password("x"), name="Beatriz")
        db.add(user)
        db.flush()
        order = _make_order(db, "comnom@ff.com", "OutroNome")
        fulfill_order(db, order)
        db.refresh(user)
        assert user.name == "Beatriz"
    finally:
        db.rollback()
        db.close()


def test_fulfill_order_novo_usuario_recebe_nome():
    db = SessionLocal()
    try:
        order = _make_order(db, "novofulfill@ff.com", "Fernanda")
        fulfill_order(db, order)
        user = db.scalar(select(User).where(User.email == "novofulfill@ff.com"))
        assert user is not None
        assert user.name == "Fernanda"
    finally:
        db.rollback()
        db.close()
