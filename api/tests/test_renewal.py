"""Testes do módulo renewal.py — lembretes de renovação e winback do Plano Lua.

Cobre:
- Plano Lua compra avulsa recebe expires_at = now + 30 dias
- Renovação estende a partir do vencimento atual, não de hoje
- oferta_plano_lua_exit também carrega prazo no plano_lua
- Outros produtos (mapa_astral) continuam vitalícios
- Cron dispara lembrete 7d
- Cron dispara lembrete today
- Cron dispara winback 2-4 dias após vencer (sem renovação)
- Cron rodando duas vezes não duplica e-mail (idempotência)
- Vencimento vitalício (expires_at NULL) nunca entra na varredura
- E-mail que falha não marca como enviado (próxima run retenta)
- Sem URL de renovação: lembrete não é enviado, não explode
- Sem URL de winback: winback silencioso, não explode
- Quem renovou não recebe winback
- Endpoint 401 com secret errado, 503 sem TASK_SECRET
- /api/me/access expõe days_remaining e needs_renewal
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import renewal as renewal_module
from app.db import SessionLocal
from app.models import Entitlement, User
from app.renewal import RenewalReminder, run_renewal_reminders
from app.security import hash_password


TASK_SECRET = "test-task-secret-abc123"
NOW = datetime.now(timezone.utc)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _task_secret(monkeypatch):
    monkeypatch.setenv("TASK_SECRET", TASK_SECRET)


@pytest.fixture()
def renewal_emails(monkeypatch):
    outbox = []
    monkeypatch.setattr(renewal_module, "send_renewal_reminder_email", lambda **kw: outbox.append(("reminder", kw)) or {"sent": True})
    monkeypatch.setattr(renewal_module, "send_winback_email", lambda **kw: outbox.append(("winback", kw)) or {"sent": True})
    return outbox


@pytest.fixture()
def renewal_emails_fail(monkeypatch):
    """Simula falha no envio de e-mail."""
    monkeypatch.setattr(renewal_module, "send_renewal_reminder_email", lambda **kw: {"sent": False, "error": "network"})
    monkeypatch.setattr(renewal_module, "send_winback_email", lambda **kw: {"sent": False, "error": "network"})


@pytest.fixture()
def renewal_url(monkeypatch):
    monkeypatch.setenv("GG_CHECKOUT_URLS", '{"site:plano_lua": "https://gg.test/plano-lua", "site:oferta_plano_lua_exit": "https://gg.test/oferta-exit"}')


def _make_user(db, email: str = "lua@test.com") -> User:
    user = User(email=email, password_hash=hash_password("senha"), name="Cliente Lua", locale="pt-BR")
    db.add(user)
    db.flush()
    return user


def _make_entitlement(db, user: User, expires_at=None, status="available") -> Entitlement:
    ent = Entitlement(
        user_id=user.id,
        product_id="site:plano_lua",
        status=status,
        source="site",
        expires_at=expires_at,
    )
    db.add(ent)
    db.commit()
    return ent


# ── Concessão com prazo (fulfill_order) ───────────────────────────────────────

GG_SECRET = "gg-secret-de-teste-32-bytes-min!"
GG_PRODUCT_MAP = '{"gg-lua": "site:plano_lua", "gg-exit": "site:oferta_plano_lua_exit"}'
GG_CHECKOUT_URLS = '{"site:plano_lua": "https://gg.test/lua", "site:oferta_plano_lua_exit": "https://gg.test/exit"}'


@pytest.fixture(autouse=True)
def _gg_env(monkeypatch):
    monkeypatch.setenv("GG_CHECKOUT_SECRET", GG_SECRET)
    monkeypatch.setenv("GG_PRODUCT_MAP", GG_PRODUCT_MAP)
    monkeypatch.setenv("GG_CHECKOUT_URLS", GG_CHECKOUT_URLS)


@pytest.fixture()
def _supress_purchase_email(monkeypatch):
    from app import checkout as site_checkout
    monkeypatch.setattr(site_checkout, "send_purchase_confirmation", lambda **kw: {"sent": True})


def _gg_payload(payment_id, email, gg_product_id="gg-lua"):
    return {
        "webhook": {"id": f"wh-{payment_id}", "businessId": "biz"},
        "payment": {"id": payment_id, "status": "paid"},
        "customer": {"email": email, "name": "Cliente"},
        "product": {"id": gg_product_id},
        "products": [{"id": gg_product_id}],
    }


def test_plano_lua_recebe_expires_at_30_dias(client, _supress_purchase_email):
    client.post(
        "/api/webhooks/ggcheckout",
        json=_gg_payload("pay-lua-prazo", "prazo@test.com"),
        headers={"x-secret": GG_SECRET},
    )
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == "prazo@test.com"))
        ent = db.scalar(select(Entitlement).where(
            Entitlement.user_id == user.id,
            Entitlement.product_id == "site:plano_lua",
        ))
        assert ent.expires_at is not None
        expires_aware = ent.expires_at if ent.expires_at.tzinfo else ent.expires_at.replace(tzinfo=timezone.utc)
        delta = expires_aware - datetime.now(timezone.utc)
        assert timedelta(days=29) < delta <= timedelta(days=30, minutes=1)
    finally:
        db.close()


def test_renovacao_estende_a_partir_do_vencimento_atual(client, _supress_purchase_email):
    """Segunda compra enquanto acesso ainda ativo: prazo soma ao vencimento existente."""
    # Primeira compra
    client.post(
        "/api/webhooks/ggcheckout",
        json=_gg_payload("pay-lua-r1", "renova@test.com"),
        headers={"x-secret": GG_SECRET},
    )
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == "renova@test.com"))
        ent = db.scalar(select(Entitlement).where(
            Entitlement.user_id == user.id,
            Entitlement.product_id == "site:plano_lua",
        ))
        expires_1 = ent.expires_at if ent.expires_at.tzinfo else ent.expires_at.replace(tzinfo=timezone.utc)
    finally:
        db.close()

    # Segunda compra (renovação enquanto ainda ativo)
    client.post(
        "/api/webhooks/ggcheckout",
        json=_gg_payload("pay-lua-r2", "renova@test.com"),
        headers={"x-secret": GG_SECRET},
    )
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == "renova@test.com"))
        ent = db.scalar(select(Entitlement).where(
            Entitlement.user_id == user.id,
            Entitlement.product_id == "site:plano_lua",
        ))
        expires_2 = ent.expires_at if ent.expires_at.tzinfo else ent.expires_at.replace(tzinfo=timezone.utc)
    finally:
        db.close()

    # Deve ser ~60 dias a partir de agora, não ~30
    delta = expires_2 - datetime.now(timezone.utc)
    assert delta > timedelta(days=58), f"Esperado >58 dias, obteve {delta}"
    # E deve ser ~30 dias após o primeiro vencimento
    extension = expires_2 - expires_1
    assert timedelta(days=29) < extension <= timedelta(days=31)


def test_oferta_exit_carrega_prazo_no_plano_lua(client, _supress_purchase_email):
    client.post(
        "/api/webhooks/ggcheckout",
        json=_gg_payload("pay-exit-01", "exit@test.com", gg_product_id="gg-exit"),
        headers={"x-secret": GG_SECRET},
    )
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == "exit@test.com"))
        ent = db.scalar(select(Entitlement).where(
            Entitlement.user_id == user.id,
            Entitlement.product_id == "site:plano_lua",
        ))
        assert ent is not None
        assert ent.expires_at is not None
        expires = ent.expires_at if ent.expires_at.tzinfo else ent.expires_at.replace(tzinfo=timezone.utc)
        assert expires > datetime.now(timezone.utc)
    finally:
        db.close()


def test_mapa_astral_continua_vitalicio(client, _supress_purchase_email, monkeypatch):
    monkeypatch.setenv("GG_PRODUCT_MAP", '{"gg-mapa": "site:mapa_astral"}')
    client.post(
        "/api/webhooks/ggcheckout",
        json=_gg_payload("pay-mapa-01", "mapa@test.com", gg_product_id="gg-mapa"),
        headers={"x-secret": GG_SECRET},
    )
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == "mapa@test.com"))
        ent = db.scalar(select(Entitlement).where(
            Entitlement.user_id == user.id,
            Entitlement.product_id == "site:mapa_astral",
        ))
        assert ent is not None
        assert ent.expires_at is None
    finally:
        db.close()


# ── Cron: varredura e lembretes ───────────────────────────────────────────────

def test_lembrete_7d_enviado(db_session, renewal_emails, renewal_url):
    user = _make_user(db_session)
    _make_entitlement(db_session, user, expires_at=NOW + timedelta(days=7))

    stats = run_renewal_reminders(db_session)

    assert stats["7d"] == 1
    assert len([e for e in renewal_emails if e[0] == "reminder"]) == 1
    assert renewal_emails[0][1]["reminder_type"] == "7d"


def test_lembrete_today_enviado(db_session, renewal_emails, renewal_url):
    user = _make_user(db_session)
    _make_entitlement(db_session, user, expires_at=NOW + timedelta(hours=3))

    stats = run_renewal_reminders(db_session)

    assert stats["today"] == 1
    assert renewal_emails[0][1]["reminder_type"] == "today"


def test_winback_enviado_3_dias_apos_vencer(db_session, renewal_emails, renewal_url):
    user = _make_user(db_session)
    _make_entitlement(db_session, user, expires_at=NOW - timedelta(days=3))

    stats = run_renewal_reminders(db_session)

    assert stats["winback"] == 1
    assert len([e for e in renewal_emails if e[0] == "winback"]) == 1


def test_cron_duas_vezes_nao_duplica_email(db_session, renewal_emails, renewal_url):
    """Idempotência: segunda execução encontra marca e não reenvia."""
    user = _make_user(db_session)
    _make_entitlement(db_session, user, expires_at=NOW + timedelta(days=7))

    run_renewal_reminders(db_session)
    run_renewal_reminders(db_session)

    assert len(renewal_emails) == 1
    assert db_session.scalar(select(RenewalReminder).where(
        RenewalReminder.reminder_type == "7d"
    )) is not None


def test_cron_winback_duas_vezes_nao_duplica(db_session, renewal_emails, renewal_url):
    user = _make_user(db_session)
    _make_entitlement(db_session, user, expires_at=NOW - timedelta(days=2))

    run_renewal_reminders(db_session)
    run_renewal_reminders(db_session)

    assert len([e for e in renewal_emails if e[0] == "winback"]) == 1


def test_entitlement_vitalicio_ignorado(db_session, renewal_emails, renewal_url):
    """expires_at NULL = vitalício; nunca entra na varredura."""
    user = _make_user(db_session)
    _make_entitlement(db_session, user, expires_at=None)

    stats = run_renewal_reminders(db_session)

    assert stats["7d"] == 0
    assert stats["today"] == 0
    assert stats["winback"] == 0
    assert renewal_emails == []


def test_email_falha_nao_marca_como_enviado(db_session, renewal_emails_fail, renewal_url):
    """Falha no envio: RenewalReminder não é criado; próxima run retenta."""
    user = _make_user(db_session)
    _make_entitlement(db_session, user, expires_at=NOW + timedelta(days=7))

    stats = run_renewal_reminders(db_session)

    assert stats["errors"] == 1
    assert stats["7d"] == 0
    assert db_session.scalar(select(RenewalReminder)) is None


def test_sem_url_renovacao_nao_envia_lembrete(db_session, renewal_emails, monkeypatch):
    monkeypatch.setenv("GG_CHECKOUT_URLS", "{}")
    user = _make_user(db_session)
    _make_entitlement(db_session, user, expires_at=NOW + timedelta(days=7))

    stats = run_renewal_reminders(db_session)

    assert stats["7d"] == 0
    assert stats["skipped"] >= 1
    assert renewal_emails == []


def test_sem_url_winback_nao_envia_winback(db_session, renewal_emails, monkeypatch):
    monkeypatch.setenv("GG_CHECKOUT_URLS", '{"site:plano_lua": "https://gg.test/lua"}')
    user = _make_user(db_session)
    _make_entitlement(db_session, user, expires_at=NOW - timedelta(days=3))

    stats = run_renewal_reminders(db_session)

    assert stats["winback"] == 0
    assert len([e for e in renewal_emails if e[0] == "winback"]) == 0


def test_renovado_nao_recebe_winback(db_session, renewal_emails, renewal_url):
    """Quem renovou tem expires_at no futuro; não cai na janela de winback."""
    user = _make_user(db_session)
    # Expires_at no futuro = já renovou
    _make_entitlement(db_session, user, expires_at=NOW + timedelta(days=20))

    stats = run_renewal_reminders(db_session)

    assert stats["winback"] == 0
    assert len([e for e in renewal_emails if e[0] == "winback"]) == 0


def test_segundo_vencimento_gera_novo_winback(db_session, renewal_emails, renewal_url):
    """Após renovação e segundo vencimento, o winback deve ser reenviado."""
    user = _make_user(db_session)
    ent = _make_entitlement(db_session, user, expires_at=NOW - timedelta(days=60))

    # Simula winback já enviado na primeira expiração (expiry_date diferente)
    db_session.add(RenewalReminder(
        entitlement_id=ent.id,
        reminder_type="winback",
        expiry_date=(NOW - timedelta(days=60)).strftime("%Y-%m-%d"),
        sent_at=NOW - timedelta(days=57),
    ))
    db_session.commit()

    # Agora o entitlement "venceu de novo" (2-4 dias atrás, data diferente)
    ent.expires_at = NOW - timedelta(days=3)
    db_session.commit()

    stats = run_renewal_reminders(db_session)

    assert stats["winback"] == 1


# ── Endpoint HTTP ─────────────────────────────────────────────────────────────

def test_endpoint_secret_errado_retorna_401(client):
    r = client.post("/api/tasks/renewal-reminders", headers={"x-task-secret": "errado"})
    assert r.status_code == 401


def test_endpoint_sem_task_secret_retorna_503(client, monkeypatch):
    monkeypatch.delenv("TASK_SECRET", raising=False)
    r = client.post("/api/tasks/renewal-reminders", headers={"x-task-secret": ""})
    assert r.status_code == 503


def test_endpoint_secret_correto_retorna_ok(client, renewal_emails, renewal_url):
    r = client.post("/api/tasks/renewal-reminders", headers={"x-task-secret": TASK_SECRET})
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ── /api/me/access com days_remaining e needs_renewal ─────────────────────────

def test_access_expoe_days_remaining_e_needs_renewal(client):
    from conftest import register
    register(client, "access@test.com", "senha123")

    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == "access@test.com"))
        db.add(Entitlement(
            user_id=user.id,
            product_id="site:plano_lua",
            status="available",
            expires_at=NOW + timedelta(days=5),
        ))
        db.commit()
    finally:
        db.close()

    r = client.get("/api/me/access")
    assert r.status_code == 200
    ents = r.json()["entitlements"]
    lua = next(e for e in ents if e["product_id"] == "site:plano_lua")

    assert 4 <= lua["days_remaining"] <= 5
    assert lua["needs_renewal"] is False
    assert lua["active"] is True


def test_access_vencido_needs_renewal_true(client):
    from conftest import register
    register(client, "vencido2@test.com", "senha123")

    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == "vencido2@test.com"))
        db.add(Entitlement(
            user_id=user.id,
            product_id="site:plano_lua",
            status="available",
            expires_at=NOW - timedelta(days=2),
        ))
        db.commit()
    finally:
        db.close()

    r = client.get("/api/me/access")
    lua = next(e for e in r.json()["entitlements"] if e["product_id"] == "site:plano_lua")
    assert lua["days_remaining"] == 0
    assert lua["needs_renewal"] is True
    assert lua["active"] is False


def test_access_vitalicio_sem_days_remaining(client):
    from conftest import register
    register(client, "vitalicio@test.com", "senha123")

    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == "vitalicio@test.com"))
        db.add(Entitlement(
            user_id=user.id,
            product_id="site:mapa_astral",
            status="available",
            expires_at=None,
        ))
        db.commit()
    finally:
        db.close()

    r = client.get("/api/me/access")
    mapa = next(e for e in r.json()["entitlements"] if e["product_id"] == "site:mapa_astral")
    assert mapa["days_remaining"] is None
    assert mapa["needs_renewal"] is None
    assert mapa["active"] is True
