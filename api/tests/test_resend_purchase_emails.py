"""Testes de run_resend_purchase_emails / POST /api/tasks/resend-purchase-emails.

GG Checkout (BR) manda um único webhook por compra — sem o segundo webhook
que o Mercado Pago dispara (payment + merchant_order), uma falha de SMTP no
instante da compra deixava a Order paga sem e-mail para sempre. Este cron
cobre isso.

Cobre:
- Reenvia e-mail de Order paga sem notified_at
- Não reenvia Order já notificada (idempotência)
- Respeita a janela mínima de idade (não compete com o webhook em andamento)
- Para de tentar após o limite de tentativas
- Reenvio reemite a senha temporária e a nova senha abre a conta
- Endpoint exige x-task-secret válido
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import checkout as checkout_module
from app.checkout import RESEND_MAX_ATTEMPTS, run_resend_purchase_emails
from app.db import SessionLocal
from app.models import Order, User
from app.security import verify_password

TASK_SECRET = "test-task-secret-resend-xyz"
NOW = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def _task_secret(monkeypatch):
    monkeypatch.setenv("TASK_SECRET", TASK_SECRET)


@pytest.fixture()
def purchase_emails(monkeypatch):
    outbox = []

    def _fake_send(**kwargs):
        outbox.append(kwargs)
        return {"sent": True}

    monkeypatch.setattr(checkout_module, "send_purchase_confirmation", _fake_send)
    return outbox


def _make_unnotified_order(email="cliente@example.com", created_delta=timedelta(minutes=30), attempts=0, product_id="site:diario_astral"):
    """Order paga, sem conta ainda (fulfill_order vai criá-la e marcar account_created)."""
    with SessionLocal() as db:
        order = Order(
            provider="ggcheckout",
            external_id=f"gg-{email}",
            product_id=product_id,
            status="paid",
            amount_minor=4990,
            currency="BRL",
            locale="pt-BR",
            market="BR",
            customer_email=email,
            raw_payload={"notify_attempts": attempts} if attempts else {},
            created_at=NOW - created_delta,
        )
        db.add(order)
        db.commit()
        db.refresh(order)
        return order.id


def _reload_order(order_id):
    with SessionLocal() as db:
        return db.get(Order, order_id)


def test_resend_delivers_email_and_marks_notified(purchase_emails):
    order_id = _make_unnotified_order()

    with SessionLocal() as db:
        stats = run_resend_purchase_emails(db, now=NOW)

    assert stats["resent"] == 1
    assert len(purchase_emails) == 1
    order = _reload_order(order_id)
    assert (order.raw_payload or {}).get("notified_at")


def test_resend_skips_order_already_notified(purchase_emails):
    order_id = _make_unnotified_order()
    # Primeira passagem entrega.
    with SessionLocal() as db:
        run_resend_purchase_emails(db, now=NOW)

    # Segunda passagem não deve reenviar.
    with SessionLocal() as db:
        stats = run_resend_purchase_emails(db, now=NOW + timedelta(hours=1))

    assert stats["resent"] == 0
    assert stats["skipped_notified"] == 1
    assert len(purchase_emails) == 1


def test_resend_skips_order_too_recent(purchase_emails):
    order_id = _make_unnotified_order(created_delta=timedelta(minutes=1))

    with SessionLocal() as db:
        stats = run_resend_purchase_emails(db, now=NOW)

    assert stats["resent"] == 0
    assert stats["skipped_too_recent"] == 1
    assert len(purchase_emails) == 0


def test_resend_stops_after_attempt_limit(purchase_emails, monkeypatch):
    # Simula SMTP sempre fora do ar: nunca marca notified_at.
    monkeypatch.setattr(checkout_module, "send_purchase_confirmation", lambda **kw: {"sent": False, "error": "smtp down"})
    order_id = _make_unnotified_order()

    for _ in range(RESEND_MAX_ATTEMPTS + 2):
        with SessionLocal() as db:
            run_resend_purchase_emails(db, now=NOW)

    with SessionLocal() as db:
        stats = run_resend_purchase_emails(db, now=NOW)

    assert stats["skipped_limit"] == 1
    order = _reload_order(order_id)
    assert not (order.raw_payload or {}).get("notified_at")
    assert (order.raw_payload or {}).get("notify_attempts") == RESEND_MAX_ATTEMPTS


def test_resend_reissues_password_and_it_opens_the_account(client, purchase_emails):
    email = "reenvio@example.com"
    order_id = _make_unnotified_order(email=email)

    with SessionLocal() as db:
        run_resend_purchase_emails(db, now=NOW)

    assert len(purchase_emails) == 1
    temp_password = purchase_emails[0].get("temp_password")
    assert temp_password

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert user is not None
        assert verify_password(temp_password, user.password_hash)

    login = client.post("/api/auth/login", json={"email": email, "password": temp_password})
    assert login.status_code == 200


def test_resend_does_not_touch_preexisting_account_password(purchase_emails):
    """Order de quem já era cliente (account_created ausente) nunca reemite senha."""
    email = "ja-era-cliente@example.com"
    with SessionLocal() as db:
        existing = User(email=email, password_hash="hash-original", name="Cliente")
        db.add(existing)
        db.commit()

    order_id = _make_unnotified_order(email=email)

    with SessionLocal() as db:
        run_resend_purchase_emails(db, now=NOW)

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert user.password_hash == "hash-original"
    assert purchase_emails[0].get("temp_password") is None


def test_endpoint_requires_valid_task_secret(client):
    r = client.post("/api/tasks/resend-purchase-emails", headers={"x-task-secret": "errado"})
    assert r.status_code == 401


def test_endpoint_503_without_task_secret_configured(client, monkeypatch):
    monkeypatch.delenv("TASK_SECRET", raising=False)
    r = client.post("/api/tasks/resend-purchase-emails", headers={"x-task-secret": ""})
    assert r.status_code == 503


def test_endpoint_runs_the_scan(client, purchase_emails):
    _make_unnotified_order()
    r = client.post("/api/tasks/resend-purchase-emails", headers={"x-task-secret": TASK_SECRET})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["resent"] == 1


def test_scan_never_reaches_back_into_old_history(purchase_emails):
    """Compra velha não pode receber e-mail do nada.

    `notified_at` é campo recente: Order anterior a ele simplesmente não tem o
    carimbo, e sem teto de idade a varredura leria essa ausência como "faltou
    entregar" — mandando para uma cliente, hoje, a confirmação de uma compra de
    meses atrás. RESEND_MAX_AGE_HOURS é o que impede isso.
    """
    from app.checkout import RESEND_MAX_AGE_HOURS

    _make_unnotified_order(
        email="antiga@example.com",
        created_delta=timedelta(hours=RESEND_MAX_AGE_HOURS + 1),
    )

    with SessionLocal() as db:
        stats = run_resend_purchase_emails(db, now=NOW)

    assert stats["resent"] == 0
    assert purchase_emails == [], "compra fora da janela não pode gerar e-mail"


def test_scan_still_covers_a_purchase_inside_the_window(purchase_emails):
    """O teto de idade não pode engolir o caso que a varredura existe para salvar."""
    from app.checkout import RESEND_MAX_AGE_HOURS

    _make_unnotified_order(
        email="dentro@example.com",
        created_delta=timedelta(hours=RESEND_MAX_AGE_HOURS - 1),
    )

    with SessionLocal() as db:
        stats = run_resend_purchase_emails(db, now=NOW)

    assert stats["resent"] == 1
    assert len(purchase_emails) == 1
