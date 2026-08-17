"""Brinde do Diário Astral (site:mapa_astral) via caminho de ASSINATURA (AR).

Regra da dona: o Diário Astral libera o Mapa Astral Completo de brinde
APENAS na primeira compra paga da cliente. Renovação e compras seguintes
não concedem de novo. Trial não recebe — o brinde só sai quando a primeira
cobrança confirma.

checkout.fulfill_order já tem essa política testada (ver
test_checkout_map_generation.py e test_checkout.py). Este arquivo cobre o
caminho paralelo — subscriptions.sync_entitlements/webhooks AR — que precisa
respeitar a MESMA política parametrizada em pricing.BUNDLE_BONUSES, senão o
caminho de assinatura contorna a regra que o checkout aplica.
"""

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import select

from app import mercadopago as mp
from app import pricing
from app.db import SessionLocal
from app.models import Entitlement, GenerationJob, Profile, Subscription, User


AR = {"email": "cliente-ar@astrodicas.com", "name": "Cliente AR"}


def _cria_assinatura_ar_pending(preapproval_id: str, *, com_perfil: bool = True) -> str:
    """Assinatura AR pendente + perfil com data/cidade de nascimento (necessário
    para enqueue_map_generation não adiar a geração por perfil incompleto)."""
    db = SessionLocal()
    try:
        user = User(
            email=AR["email"],
            password_hash="placeholder",
            name=AR["name"],
            locale="es-AR",
        )
        db.add(user)
        db.flush()
        if com_perfil:
            db.add(Profile(user_id=user.id, birth_date=date(1990, 5, 20), birth_city="Recife", birth_country="BR"))
        sub = Subscription(
            user_id=user.id,
            provider="mercadopago",
            external_id=preapproval_id,
            product_id="site:diario_astral",
            status="pending",
            amount_minor=pricing.amount_minor("site:diario_astral", "es-AR"),
            currency="ARS",
            locale="es-AR",
            market="AR",
        )
        db.add(sub)
        db.commit()
        return sub.id
    finally:
        db.close()


def _autoriza_preapproval(client, monkeypatch, preapproval_id: str) -> None:
    monkeypatch.setattr(
        mp,
        "get_preapproval",
        lambda pid: {"id": pid, "status": "authorized", "external_reference": preapproval_id},
    )
    client.post(
        "/api/webhooks/mercadopago/ar/subscription",
        json={"type": "subscription_preapproval", "data": {"id": preapproval_id}},
    )


def _paga(client, monkeypatch, preapproval_id: str, payment_id: str) -> None:
    monkeypatch.setattr(
        mp,
        "get_payment",
        lambda pid: {"id": pid, "status": "approved", "metadata": {"preapproval_id": preapproval_id}},
    )
    client.post(
        "/api/webhooks/mercadopago/ar/subscription",
        json={"type": "subscription_authorized_payment", "data": {"id": payment_id}},
    )


def _assinatura_ar_paga(client, monkeypatch, *, com_perfil: bool = True, preapproval_id: str | None = None) -> str:
    preapproval_id = preapproval_id or f"preapproval-{uuid4()}"
    sub_id = _cria_assinatura_ar_pending(preapproval_id, com_perfil=com_perfil)
    _autoriza_preapproval(client, monkeypatch, preapproval_id)
    _paga(client, monkeypatch, preapproval_id, f"payment-1-{preapproval_id}")
    return sub_id, preapproval_id


def _mapa_entitlement(db):
    return db.scalar(select(Entitlement).where(Entitlement.product_id == "site:mapa_astral"))


def _jobs_mapa(db, user_id: str):
    return db.scalars(
        select(GenerationJob).where(
            GenerationJob.user_id == user_id, GenerationJob.content_id == "site:content:mapa_astral_completo"
        )
    ).all()


# ---------------------------------------------------------------------------
# (a) primeira compra concede o brinde e enfileira a geração
# ---------------------------------------------------------------------------

def test_primeira_compra_concede_mapa_e_enfileira_geracao(client, monkeypatch):
    sub_id, _ = _assinatura_ar_paga(client, monkeypatch)

    db = SessionLocal()
    try:
        sub = db.get(Subscription, sub_id)
        ent = _mapa_entitlement(db)
        assert ent is not None, "primeira cobrança confirmada deve conceder o brinde"
        assert ent.status == "available"
        assert ent.expires_at is None, "brinde é vitalício"

        jobs = _jobs_mapa(db, sub.user_id)
        assert len(jobs) == 1, "geração do mapa deve ser enfileirada na primeira concessão"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# (b) renovação (segunda compra/cobrança) não concede de novo nem reenfileira
# ---------------------------------------------------------------------------

def test_renovacao_nao_reconcede_nem_reenfileira_geracao(client, monkeypatch):
    sub_id, preapproval_id = _assinatura_ar_paga(client, monkeypatch)

    db = SessionLocal()
    try:
        sub = db.get(Subscription, sub_id)
        user_id = sub.user_id
        ent_antes = _mapa_entitlement(db)
        ent_id_antes = ent_antes.id
        jobs_antes = len(_jobs_mapa(db, user_id))
        assert jobs_antes == 1
    finally:
        db.close()

    # Segunda cobrança confirmada (renovação do mês 2).
    _paga(client, monkeypatch, preapproval_id, "payment-2")

    db = SessionLocal()
    try:
        ent_depois = _mapa_entitlement(db)
        assert ent_depois.id == ent_id_antes, "não deve recriar o entitlement na renovação"
        assert ent_depois.expires_at is None

        jobs_depois = _jobs_mapa(db, user_id)
        assert len(jobs_depois) == jobs_antes, "renovação não reenfileira geração do brinde já concedido"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# (c) trial não recebe o brinde
# ---------------------------------------------------------------------------

def test_trial_nao_concede_mapa_astral(client):
    response = client.post(
        "/api/trial/start",
        json={"email": "trial-sem-brinde@astrodicas.com", "name": "Trial", "locale": "es-AR"},
    )
    assert response.status_code == 200, response.text

    db = SessionLocal()
    try:
        assert _mapa_entitlement(db) is None, "trial sem cobrança confirmada não recebe o brinde"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# (d) quem já comprou o mapa avulso antes e depois assina não ganha
# duplicado nem perde o que já tinha
# ---------------------------------------------------------------------------

def test_quem_ja_tinha_mapa_avulso_nao_ganha_duplicado_nem_perde_o_que_tinha(client, monkeypatch):
    preapproval_id = f"preapproval-{uuid4()}"
    db = SessionLocal()
    try:
        user = User(email=AR["email"], password_hash="placeholder", name=AR["name"], locale="es-AR")
        db.add(user)
        db.flush()
        db.add(Profile(user_id=user.id, birth_date=date(1990, 5, 20), birth_city="Recife", birth_country="BR"))
        entitlement_avulso = Entitlement(
            user_id=user.id, product_id="site:mapa_astral", status="available", source="site", expires_at=None
        )
        db.add(entitlement_avulso)
        sub = Subscription(
            user_id=user.id,
            provider="mercadopago",
            external_id=preapproval_id,
            product_id="site:diario_astral",
            status="pending",
            amount_minor=pricing.amount_minor("site:diario_astral", "es-AR"),
            currency="ARS",
            locale="es-AR",
            market="AR",
        )
        db.add(sub)
        db.commit()
        ent_id_antes = entitlement_avulso.id
        user_id = user.id
    finally:
        db.close()

    _autoriza_preapproval(client, monkeypatch, preapproval_id)
    _paga(client, monkeypatch, preapproval_id, "payment-1")

    db = SessionLocal()
    try:
        entitlements_mapa = db.scalars(
            select(Entitlement).where(Entitlement.user_id == user_id, Entitlement.product_id == "site:mapa_astral")
        ).all()
        assert len(entitlements_mapa) == 1, "assinar depois de já ter o mapa avulso não duplica o entitlement"
        assert entitlements_mapa[0].id == ent_id_antes
        assert entitlements_mapa[0].status == "available", "o que já tinha continua disponível"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# (e) parametrização de verdade: mudar a política declarada muda o
# comportamento — não só o caminho feliz
# ---------------------------------------------------------------------------

def test_mudar_policy_para_always_reconcede_a_cada_renovacao(client, monkeypatch):
    """Se a dona decidir amanhã que o brinde vira promoção recorrente (policy="always"),
    sync_entitlements deve reconceder/reenfileirar a cada cobrança — não travar como "once"."""
    always_bonus = pricing.BundleBonus("site:diario_astral", "site:mapa_astral", policy="always")
    monkeypatch.setattr(pricing, "BUNDLE_BONUSES", (always_bonus,))

    sub_id, preapproval_id = _assinatura_ar_paga(client, monkeypatch)

    db = SessionLocal()
    try:
        sub = db.get(Subscription, sub_id)
        user_id = sub.user_id
        ent = _mapa_entitlement(db)
        ent.status = "revoked"  # simula revogação manual entre cobranças
        db.commit()
    finally:
        db.close()

    _paga(client, monkeypatch, preapproval_id, "payment-2")

    db = SessionLocal()
    try:
        ent_depois = _mapa_entitlement(db)
        assert ent_depois.status == "available", "policy=always deve reativar o brinde na renovação seguinte"

        jobs_depois = _jobs_mapa(db, user_id)
        assert len(jobs_depois) == 2, "policy=always reenfileira geração a cada reconcessão"
    finally:
        db.close()


def test_bonus_policy_desconhecida_e_always_por_padrao():
    """Bundle sem entrada em BUNDLE_BONUSES segue o comportamento padrão de bundle comum."""
    assert pricing.bonus_policy("site:combo_mapa_astral_amor", "site:mapa_amor_sinastria") == "always"
    assert pricing.bonus_policy("site:diario_astral", "site:mapa_astral") == "once"
