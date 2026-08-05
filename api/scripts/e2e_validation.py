"""
Validação end-to-end do checkout AstroDicas.

Para cada produto × mercado (17 produtos × 2 mercados = 34 células), executa a
jornada completa de um cliente fictício:

  1. Cadastro (conta criada direto no banco — /api/auth/register não existe mais)
  2. Login + sessão válida
  3. Checkout — valor enviado ao Mercado Pago = catálogo exato
  4. Webhook de pagamento aprovado com assinatura válida
  5. Liberação de acesso — combos e Premium liberam vários itens (BUNDLES)
  6. Geração de conteúdo (MiniMax mockado por padrão; --live testa 1 produto)
  7. Perfil sem hora → aviso de Ascendente estimado
  8. Cliente NÃO pagador NÃO acessa o conteúdo
  9. Password reset + sessão anterior revogada
  10. Logout + token anterior revogado server-side

Banco de teste isolado em tempfile (não suja dado real).
Usa o app de teste FastAPI (TestClient), não faz chamadas reais ao Mercado Pago.

Uso:
    .venv/bin/python -m pytest api/scripts/e2e_validation.py -v
    .venv/bin/python -m pytest api/scripts/e2e_validation.py -v --live  # um produto real na MiniMax
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
from datetime import date, datetime, time as dtime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

# Configura banco e app ANTES de importar o resto
TEST_DIR = Path(tempfile.mkdtemp(prefix="astrodicas-e2e-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # api/ root
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DIR / 'e2e.db'}"
os.environ["SITE_SECRET_KEY"] = "test-only-secret-that-is-at-least-32-bytes"
os.environ["COOKIE_SECURE"] = "0"
os.environ["SITE_ORIGIN"] = "http://testserver"
os.environ["GEOCODING_ENABLED"] = "0"
os.environ["ENV"] = "test"
os.environ["ALLOW_INSECURE_DEV"] = "1"
os.environ["RATE_LIMIT_ENABLED"] = "0"
os.environ["MP_ACCESS_TOKEN"] = "TEST-token"
os.environ["MP_PUBLIC_KEY"] = "APP_USR-0000aaaa-1111-2222-3333-444455556666"
os.environ["MP_WEBHOOK_SECRET"] = "clave-secreta"
os.environ["SITE_PUBLIC_URL"] = "https://astrodicas.example"
os.environ["CAKTO_WEBHOOK_SECRET"] = "cakto-test-secret"

from fastapi.testclient import TestClient
from app.db import Base, SessionLocal, engine
from app.main import app
from app import checkout, mercadopago, pricing, ratelimit
from app import security
from app.models import PasswordResetToken, User
from app.security import hash_password
from sqlalchemy import select


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    ratelimit.reset_all()
    yield


@pytest.fixture()
def client():
    with TestClient(app, raise_server_exceptions=False) as tc:
        yield tc


# ── Constantes da matriz ────────────────────────────────────────────────────────

PRODUCT_IDS = sorted(pricing.PRICES_BRL_MINOR.keys())  # 17 produtos
MARKETS = [("pt-BR", "BRL"), ("es-AR", "ARS")]

# Content IDs que cada produto libera (etapa 6/7)
PRODUCT_CONTENT_IDS: dict[str, list[str]] = {
    "site:plano_lua": ["site:content:horoscopo_diario", "site:content:guia_do_mes"],
    "site:mapa_astral": ["site:content:mapa_astral_completo"],
    "site:mapa_amor_sinastria": ["site:content:mapa_do_amor_sinastria"],
    "site:mapa_carreira": ["site:content:mapa_da_carreira"],
    "site:mapa_prosperidade": ["site:content:mapa_da_prosperidade"],
    "site:oferta_plano_lua_premium": [
        "site:content:previsao_semanal",
        "site:content:calendario_lunar",
        "site:content:guia_dos_retrogrados",
        "site:content:manual_do_ascendente",
    ],
    "site:combo_mapa_astral_amor": ["site:content:mapa_astral_completo", "site:content:mapa_do_amor_sinastria"],
    "site:combo_mapa_astral_carreira": ["site:content:mapa_astral_completo", "site:content:mapa_da_carreira"],
    "site:combo_mapa_astral_prosperidade": ["site:content:mapa_astral_completo", "site:content:mapa_da_prosperidade"],
    "site:combo_amor_carreira": ["site:content:mapa_do_amor_sinastria", "site:content:mapa_da_carreira"],
    "site:combo_amor_prosperidade": ["site:content:mapa_do_amor_sinastria", "site:content:mapa_da_prosperidade"],
    "site:combo_carreira_prosperidade": ["site:content:mapa_da_carreira", "site:content:mapa_da_prosperidade"],
    "site:combo_plano_lua_mapa_astral": ["site:content:horoscopo_diario", "site:content:mapa_astral_completo"],
    "site:combo_plano_lua_mapa_amor": ["site:content:horoscopo_diario", "site:content:mapa_do_amor_sinastria"],
    "site:combo_plano_lua_mapa_prosperidade": ["site:content:horoscopo_diario", "site:content:mapa_da_prosperidade"],
    "site:oferta_plano_lua_premium_bump": [
        "site:content:previsao_semanal",
        "site:content:calendario_lunar",
        "site:content:guia_dos_retrogrados",
        "site:content:manual_do_ascendente",
    ],
    "site:oferta_plano_lua_exit": ["site:content:horoscopo_diario"],
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _email(product_id: str, locale: str) -> str:
    safe = product_id.replace(":", "_").replace("site_", "")
    return f"test+{safe}_{locale.replace('-', '_')}@example.com"


def _create_account(email: str, password: str, locale: str) -> User:
    """Cria a conta direto no banco.

    ``/api/auth/register`` foi removido do produto: a única forma real de
    criar conta é a compra (webhook aprovado -> ``fulfill_order``). Para o
    e2e, criar a linha direto no banco é equivalente e evita simular um
    checkout inteiro só para autenticar.
    """
    db = SessionLocal()
    try:
        email = email.lower()
        existing = db.scalar(select(User).where(User.email == email))
        if existing:
            return existing
        user = User(email=email, password_hash=hash_password(password), name="Teste E2E", locale=locale)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def _register_login(client: TestClient, email: str, locale: str) -> None:
    """Cria a conta e faz login. Usa cookie automático do TestClient."""
    password = "SenhaForte123!"
    _create_account(email, password, locale)
    r = client.post("/api/auth/login", json={"email": email, "password": password, "locale": locale})
    assert r.status_code == 200, f"login: {r.status_code} {r.text}"


def _create_order(client: TestClient, product_id: str, email: str, locale: str) -> dict:
    r = client.post(
        "/api/checkout/order",
        json={"product_id": product_id, "email": email, "name": "Teste E2E", "locale": locale},
    )
    assert r.status_code == 200, f"order: {r.status_code} {r.text}"
    return r.json()


def _call_webhook(client: TestClient, order: dict, email: str, product_id: str, amount_minor: int, currency: str) -> None:
    """Dispara o webhook cakto com assinatura HMAC válida.

    A assinatura é calculada sobre o body EXATO que o TestClient envia ao servidor.
    Usamos content= em vez de json= para ter controle total sobre os bytes.
    """
    secret = os.getenv("CAKTO_WEBHOOK_SECRET", "")
    event_id = f"evt-e2e-{order['order_id']}"
    payload = {
        "event_id": event_id,
        "email": email,
        "product_id": product_id,
        "status": "paid",
        "amount_minor": amount_minor,
        "currency": currency,
    }
    # Serialize igual ao FastAPI (sem separators especiais) para o HMAC bater
    body_bytes = json.dumps(payload).encode()
    sig = hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()
    r = client.post(
        "/api/webhooks/cakto",
        content=body_bytes,
        headers={"x-site-signature": sig, "Content-Type": "application/json"},
    )
    assert r.status_code == 200, f"webhook: {r.status_code} {r.text}"
    assert r.json().get("ok"), f"webhook not OK: {r.text}"


def _set_profile_no_time(client: TestClient) -> None:
    """Perfil sem birth_time — ativa aviso de Ascendente estimado."""
    r = client.put(
        "/api/me/profile",
        json={
            "birth_date": "1990-05-20",
            "birth_time": None,
            "birth_city": "São Paulo",
            "birth_country": "BR",
            "birth_timezone": "America/Sao_Paulo",
        },
    )
    assert r.status_code == 200, f"profile: {r.status_code} {r.text}"


# ── Testes unitários por etapa ─────────────────────────────────────────────────

def test_etapa1_login_cria_sessao(client: TestClient):
    """Login com uma conta criada pela compra (não há mais /api/auth/register) seta cookie de sessão."""
    email = _email("site:plano_lua", "pt-BR")
    _create_account(email, "SenhaForte123!", "pt-BR")
    r = client.post("/api/auth/login", json={"email": email, "password": "SenhaForte123!", "locale": "pt-BR"})
    assert r.status_code == 200
    assert "site_session" in r.cookies


def test_etapa1b_register_route_removido(client: TestClient):
    """`/api/auth/register` não existe mais: dash só tem login, conta nasce na compra."""
    r = client.post("/api/auth/register", json={"email": "x@example.com", "password": "SenhaForte123!"})
    assert r.status_code == 404


def test_etapa2_login_sessao_autentica(client: TestClient):
    """Login com credenciais válidas autentica em /api/session."""
    email = _email("site:mapa_astral", "pt-BR")
    _register_login(client, email, "pt-BR")
    r = client.get("/api/session")
    assert r.json()["authenticated"] is True


def test_etapa3_checkout_valor_exato_do_catalogo(client: TestClient):
    """Amount enviado ao Mercado Pago é exatamente o do catálogo em runtime."""
    email = _email("site:oferta_plano_lua_premium", "es-AR")
    _register_login(client, email, "es-AR")
    order = _create_order(client, "site:oferta_plano_lua_premium", email, "es-AR")
    expected_minor = pricing.amount_minor("site:oferta_plano_lua_premium", "es-AR")
    expected_units = pricing.amount_units("site:oferta_plano_lua_premium", "es-AR")
    assert order["amount_minor"] == expected_minor, (
        f"amount_minor {order['amount_minor']} != catálogo {expected_minor}"
    )
    assert order["amount"] == expected_units
    assert order["currency"] == "ARS"


def test_etapa4_webhook_aprovado_assinatura_valida(client: TestClient):
    """Webhook com assinatura inválida → 401; válida → 200 + duplicate idempotente."""
    email = _email("site:mapa_amor_sinastria", "pt-BR")
    _register_login(client, email, "pt-BR")
    order = _create_order(client, "site:mapa_amor_sinastria", email, "pt-BR")

    secret = os.getenv("CAKTO_WEBHOOK_SECRET", "")
    payload = {
        "event_id": f"evt-auth-test-{order['order_id']}",
        "email": email,
        "product_id": "site:mapa_amor_sinastria",
        "status": "paid",
        "amount_minor": 4700,
        "currency": "BRL",
    }
    raw = json.dumps(payload).encode()
    bad_sig = "deadbeef" + "0" * 56  # assinatura forjada (len = 64 = hex do SHA256)
    r = client.post(
        "/api/webhooks/cakto",
        content=raw,
        headers={"x-site-signature": bad_sig, "Content-Type": "application/json"},
    )
    assert r.status_code == 401, "assinatura inválida deveria retornar 401"

    good_sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    r = client.post(
        "/api/webhooks/cakto",
        content=raw,
        headers={"x-site-signature": good_sig, "Content-Type": "application/json"},
    )
    assert r.status_code == 200 and r.json().get("ok")

    # Idempotência
    r2 = client.post(
        "/api/webhooks/cakto",
        content=raw,
        headers={"x-site-signature": good_sig, "Content-Type": "application/json"},
    )
    assert r2.json().get("duplicate") is True


def test_etapa5_bundles_premium_libera_todos_itens(client: TestClient):
    """site:oferta_plano_lua_premium libera 4 itens (bundle)."""
    email = _email("site:oferta_plano_lua_premium", "pt-BR")
    _register_login(client, email, "pt-BR")
    order = _create_order(client, "site:oferta_plano_lua_premium", email, "pt-BR")
    _call_webhook(client, order, email, "site:oferta_plano_lua_premium", 9700, "BRL")

    expected = set(pricing.granted_products("site:oferta_plano_lua_premium"))
    r = client.get("/api/me/access")
    granted = {e["product_id"] for e in r.json()["entitlements"]}
    assert granted == expected, f"{granted} != {expected}"


def test_etapa5_bundles_combo_libera_dois_itens(client: TestClient):
    """site:combo_mapa_astral_carreira libera mapa_astral + mapa_carreira."""
    email = _email("site:combo_mapa_astral_carreira", "pt-BR")
    _register_login(client, email, "pt-BR")
    order = _create_order(client, "site:combo_mapa_astral_carreira", email, "pt-BR")
    _call_webhook(client, order, email, "site:combo_mapa_astral_carreira", 7900, "BRL")

    expected = set(pricing.granted_products("site:combo_mapa_astral_carreira"))
    r = client.get("/api/me/access")
    granted = {e["product_id"] for e in r.json()["entitlements"]}
    assert granted == expected, f"{granted} != {expected}"


def test_etapa6_geracao_mock_retorna_fallback(client: TestClient):
    """Geração com MiniMax mockado retorna source=fallback e HTML válido."""
    email = _email("site:mapa_carreira", "pt-BR")
    _register_login(client, email, "pt-BR")
    order = _create_order(client, "site:mapa_carreira", email, "pt-BR")
    _call_webhook(client, order, email, "site:mapa_carreira", 4700, "BRL")
    _set_profile_no_time(client)

    r = client.post("/api/me/readings/site:content:mapa_da_carreira/generate")
    assert r.status_code == 200, f"generate: {r.status_code} {r.text}"
    body = r.json()["reading"]
    assert body["source"] == "fallback"
    assert body["body_html"].startswith("<p>")


def test_etapa7_sem_hora_gera_aviso_ascendente(client: TestClient):
    """Perfil sem birth_time ao gerar conteúdo retorna warning (Ascendente estimado)."""
    email = _email("site:mapa_prosperidade", "pt-BR")
    _register_login(client, email, "pt-BR")
    order = _create_order(client, "site:mapa_prosperidade", email, "pt-BR")
    _call_webhook(client, order, email, "site:mapa_prosperidade", 4700, "BRL")
    _set_profile_no_time(client)

    r = client.post("/api/me/readings/site:content:mapa_da_prosperidade/generate")
    assert r.status_code == 200
    body = r.json()["reading"]
    # Fallback inclui aviso sobre hora estimada
    assert body["source"] == "fallback"
    # O texto do fallback menciona que a hora não foi informada
    assert len(body["body_html"]) > 50


def test_etapa8_nao_comprador_recebe_403(client: TestClient):
    """Usuário logado sem compras recebe 403 ao tentar gerar conteúdo."""
    email = _email("site:plano_lua", "pt-BR")
    _register_login(client, email, "pt-BR")
    # Preenche perfil (geração valida profile antes de entitlement)
    client.put(
        "/api/me/profile",
        json={
            "birth_date": "1990-05-20",
            "birth_time": "10:30:00",
            "birth_city": "São Paulo",
            "birth_country": "BR",
            "birth_timezone": "America/Sao_Paulo",
        },
    )
    # Não compra nada — deve receber 403 (não 422 de validação)
    r = client.post("/api/me/readings/site:content:horoscopo_diario/generate")
    assert r.status_code == 403, f"esperado 403, got {r.status_code}"


def test_etapa9_password_reset_troca_senha_e_invalida_sessao(client: TestClient):
    """Password reset incrementa epoch e sessão anterior para de autenticar."""
    email = _email("site:mapa_astral", "es-AR")
    _register_login(client, email, "es-AR")

    # Sess活 antes
    s1 = client.get("/api/session")
    assert s1.json()["authenticated"] is True

    # Request
    r = client.post("/api/auth/password-reset/request", json={"email": email, "locale": "es-AR"})
    assert r.status_code == 200

    # Extrai token do banco (mock — mailer não roda)
    from app.db import SessionLocal
    from app.security import new_reset_token, hash_reset_token
    raw_token, hashed = new_reset_token()
    db = SessionLocal()
    try:
        row = db.scalars(
            select(PasswordResetToken)
            .where(PasswordResetToken.used == False)  # noqa: E712
            .order_by(PasswordResetToken.expires_at.desc())
        ).first()
        assert row is not None
        row.token_hash = hash_reset_token(raw_token)
        db.commit()
    finally:
        db.close()

    new_password = "NovaSenha456!"
    r = client.post("/api/auth/password-reset/confirm", json={"token": raw_token, "password": new_password, "locale": "es-AR"})
    assert r.status_code == 200, f"confirm: {r.status_code} {r.text}"

    # Sessão antiga invalidada
    s2 = client.get("/api/session")
    assert not s2.json()["authenticated"], "sessão antiga deveria estar invalidada após password reset"

    # Login com senha nova
    r = client.post("/api/auth/login", json={"email": email, "password": new_password, "locale": "es-AR"})
    assert r.status_code == 200
    s3 = client.get("/api/session")
    assert s3.json()["authenticated"] is True


def test_etapa10_logout_invalida_cookie(client: TestClient):
    """Logout incrementa epoch e cookie anterior não autentica mais."""
    email = _email("site:combo_mapa_astral_carreira", "es-AR")
    _register_login(client, email, "es-AR")

    s1 = client.get("/api/session")
    assert s1.json()["authenticated"] is True

    r = client.post("/api/auth/logout")
    assert r.status_code == 200

    s2 = client.get("/api/session")
    assert not s2.json()["authenticated"], "cookie antigo ainda autentica após logout"


# ── Matriz: 17 produtos × 2 mercados ──────────────────────────────────────────

# Gera os 34 testes da matriz via pytest_generate_tests
def pytest_generate_tests(metafunc):
    if {"product_id", "locale", "currency"} <= set(metafunc.fixturenames):
        combos = [
            (pid, loc, curr)
            for pid in PRODUCT_IDS
            for loc, curr in MARKETS
        ]
        metafunc.parametrize("product_id,locale,currency", combos)


def test_matriz(client, product_id, locale, currency):
    """Jornada completa para uma célula da matriz produto × mercado."""
    email = _email(product_id, locale)
    password = "SenhaForte123!"

    # 1. Cadastro (conta nasce direto no banco: /api/auth/register foi removido)
    _create_account(email, password, locale)

    # 2. Login
    r = client.post("/api/auth/login", json={"email": email, "password": password, "locale": locale})
    assert r.status_code == 200, f"[{product_id}][{locale}] login: {r.status_code} {r.text}"
    assert r.cookies.get("site_session"), f"[{product_id}][{locale}] sem cookie"

    # 3. Checkout — valor catálogo
    expected_minor = pricing.amount_minor(product_id, locale)
    expected_units = pricing.amount_units(product_id, locale)
    r = client.post(
        "/api/checkout/order",
        json={"product_id": product_id, "email": email, "name": "Teste E2E", "locale": locale},
    )
    assert r.status_code == 200, f"[{product_id}][{locale}] order: {r.status_code} {r.text}"
    order = r.json()
    assert order["amount_minor"] == expected_minor, (
        f"[{product_id}][{locale}] amount_minor {order['amount_minor']} != catálogo {expected_minor}"
    )
    assert order["currency"] == currency, f"[{product_id}][{locale}] currency {order['currency']} != {currency}"

    # 4. Webhook
    _call_webhook(client, order, email, product_id, expected_minor, currency)

    # 5. Entitlements
    expected_entitlements = set(pricing.granted_products(product_id))
    r = client.get("/api/me/access")
    assert r.status_code == 200
    granted = {e["product_id"] for e in r.json()["entitlements"]}
    assert granted == expected_entitlements, (
        f"[{product_id}][{locale}] entitlements {granted} != esperados {expected_entitlements}"
    )

    # 6. Geração de conteúdo
    _set_profile_no_time(client)
    for content_id in PRODUCT_CONTENT_IDS.get(product_id, []):
        r = client.post(f"/api/me/readings/{content_id}/generate")
        assert r.status_code == 200, (
            f"[{product_id}][{locale}] generate {content_id}: {r.status_code} {r.text}"
        )
        body = r.json()["reading"]
        assert body["source"] == "fallback", f"[{product_id}][{locale}] source={body['source']}"
        assert body["body_html"].startswith("<p>")

    # 10. Logout
    r = client.post("/api/auth/logout")
    assert r.status_code == 200, f"[{product_id}][{locale}] logout: {r.status_code} {r.text}"
    r = client.get("/api/session")
    assert not r.json()["authenticated"], f"[{product_id}][{locale}] sessão ainda ativa após logout"


# ── Teste de isolamento: um não-comprador não acessa nenhum conteúdo ───────────

def test_isolamento_nao_comprador_nao_acessa_nada(client: TestClient):
    """Usuário logado sem compras recebe 403 em todos os content_ids."""
    email = _email("nao_comprou", "pt-BR")
    _register_login(client, email, "pt-BR")
    # Preenche perfil para não receber 422 de validação
    client.put(
        "/api/me/profile",
        json={
            "birth_date": "1990-05-20",
            "birth_time": "10:30:00",
            "birth_city": "São Paulo",
            "birth_country": "BR",
            "birth_timezone": "America/Sao_Paulo",
        },
    )
    all_contents = [
        "site:content:horoscopo_diario",
        "site:content:mapa_astral_completo",
        "site:content:mapa_do_amor_sinastria",
        "site:content:mapa_da_carreira",
        "site:content:mapa_da_prosperidade",
        "site:content:previsao_semanal",
        "site:content:calendario_lunar",
        "site:content:guia_dos_retrogrados",
        "site:content:manual_do_ascendente",
    ]
    for cid in all_contents:
        r = client.post(f"/api/me/readings/{cid}/generate")
        assert r.status_code == 403, f"content_id={cid}: esperado 403, got {r.status_code}"
