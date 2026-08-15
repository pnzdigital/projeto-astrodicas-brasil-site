"""Quem tem acesso e não tem perfil precisa aparecer, não sumir num contador.

`run_daily_pregen` pula quem não tem data e cidade de nascimento — correto, sem
esses dados não há mapa para calcular. O problema era o pulo ser MUDO: em
14 e 15/08/2026 a task reportou `skipped_no_profile: 10` nas duas execuções,
e não havia como saber QUEM eram. São pessoas com entitlement ativo que não
recebem leitura nenhuma; quem pagou conclui que o produto não funciona.

Contrato coberto aqui:
- cliente com acesso e sem perfil entra em stats["sem_perfil"] com e-mail e produto
- cliente com perfil completo NÃO entra
- o contador antigo (skipped_no_profile) continua batendo com a lista
"""

from __future__ import annotations

from app.db import SessionLocal
from app.models import Entitlement, Profile, User
from app.renewal import run_daily_pregen
from conftest import register as _register

PRODUCT_ID = "site:diario_astral"


def _cria_cliente(client, email: str, com_perfil: bool) -> None:
    r = _register(client, email, "senha-segura", name="Cliente Teste", locale="pt-BR")
    assert r.status_code == 200
    if com_perfil:
        client.put(
            "/api/me/profile",
            json={
                "birth_date": "1988-04-10",
                "birth_time": "08:00:00",
                "birth_city": "São Paulo",
                "birth_country": "BR",
            },
        )
    r = client.post(
        "/api/webhooks/cakto",
        json={"event_id": f"evt-{email}", "email": email, "product_id": PRODUCT_ID},
    )
    assert r.status_code == 200


def test_cliente_sem_perfil_aparece_identificado(client, monkeypatch):
    monkeypatch.setenv("HOROSCOPE_PREGEN_ENABLED", "1")
    _cria_cliente(client, "sem-perfil@example.com", com_perfil=False)

    with SessionLocal() as db:
        stats = run_daily_pregen(db)

    sem_perfil = stats.get("sem_perfil", [])
    emails = [item["email"] for item in sem_perfil]
    assert "sem-perfil@example.com" in emails, (
        "cliente com acesso e sem dados de nascimento precisa ser identificável, "
        f"não só contado; veio: {stats}"
    )
    # A lista tem que bater com o contador — se divergir, alguém mexeu num sem o outro.
    assert len(sem_perfil) == stats["skipped_no_profile"]

    registro = next(i for i in sem_perfil if i["email"] == "sem-perfil@example.com")
    assert registro["product_id"] == PRODUCT_ID
    assert registro["user_id"]


def test_cliente_com_perfil_nao_entra_na_lista(client, monkeypatch):
    monkeypatch.setenv("HOROSCOPE_PREGEN_ENABLED", "1")
    _cria_cliente(client, "com-perfil@example.com", com_perfil=True)

    with SessionLocal() as db:
        stats = run_daily_pregen(db)

    emails = [item["email"] for item in stats.get("sem_perfil", [])]
    assert "com-perfil@example.com" not in emails
