"""Fallback nunca é leitura pronta — a próxima visita tenta de novo.

Mas para evitar loop de regeneração cara no LLM, o sistema limita a 3
tentativas por 24h. Na 4ª chamada serve o fallback existente como 200.

Cobre: Mapa Astral (conteúdo "permanente", reading_is_current True) e
Horóscopo Diário (conteúdo com regra de tempo — amanhã cura sozinho).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import Entitlement, Reading
from conftest import register as _register

MAPA = "site:content:mapa_astral_completo"
HORO = "site:content:horoscopo_diario"


def _setup(client, db_session, email: str, products: list[str]):
    _register(client, email, "senha-segura", name="Cliente Teste")
    client.put(
        "/api/me/profile",
        json={"birth_date": "1990-05-20", "birth_time": "14:30:00", "birth_city": "São Paulo", "birth_country": "BR"},
    )
    for pid in products:
        client.post("/api/webhooks/cakto", json={"event_id": f"evt-{pid}-{email}", "email": email, "product_id": pid})


def _generate(client, content_id: str):
    return client.post(f"/api/me/readings/{content_id}/generate")


def _fallbacks_in_db(db_session, user_email: str, content_id: str) -> list[Reading]:
    from app.models import User
    from sqlalchemy import select

    user = db_session.scalar(select(User).where(User.email == user_email))
    return db_session.scalars(
        select(Reading).where(
            Reading.user_id == user.id,
            Reading.content_id == content_id,
            Reading.status == "fallback",
        )
    ).all()


# ── Mapa Astral (permanente) ────────────────────────────────────────────────

def test_mapa_astral_fallback_tenta_de_novo_nas_tres_primeiras_vezes(client, db_session):
    """Cada chamada com fallback existente cria nova tentativa (202) até o limite."""
    email = "retry-mapa@example.com"
    _setup(client, db_session, email, ["site:oferta_plano_lua_premium"])

    for i in range(3):
        r = _generate(client, MAPA)
        assert r.status_code == 202, f"Tentativa {i+1}: esperava 202, obteve {r.status_code}: {r.text}"
        reading = next(
            x for x in client.get("/api/me/readings").json()["readings"] if x["content_id"] == MAPA
        )
        assert reading["status"] == "fallback"

    # Após 3 fallbacks em 24h, a 4ª serve o existente como 200
    r4 = _generate(client, MAPA)
    assert r4.status_code == 200, f"4ª tentativa: esperava 200 (limite atingido), obteve {r4.status_code}"
    assert r4.json()["reading"]["status"] == "fallback"


def test_mapa_astral_fallback_antigo_mais_de_24h_permite_nova_tentativa(client, db_session):
    """Fallback com mais de 24h não conta no limite — pode tentar de novo."""
    from sqlalchemy import select
    from app.models import User

    email = "retry-antigo@example.com"
    _setup(client, db_session, email, ["site:oferta_plano_lua_premium"])

    # Gera 3 fallbacks e força o created_at para > 24h atrás
    for _ in range(3):
        _generate(client, MAPA)

    user = db_session.scalar(select(User).where(User.email == email))
    old_cutoff = datetime.now(timezone.utc) - timedelta(hours=25)
    readings = db_session.scalars(select(Reading).where(Reading.user_id == user.id, Reading.content_id == MAPA)).all()
    for r in readings:
        r.created_at = old_cutoff
    db_session.commit()

    # Agora há 0 fallbacks nas últimas 24h → deve permitir nova tentativa
    r = _generate(client, MAPA)
    assert r.status_code == 202, f"Após resetar 24h: esperava 202, obteve {r.status_code}"


# ── Horóscopo Diário (temporal) ──────────────────────────────────────────────

def test_horoscopo_fallback_de_hoje_tenta_de_novo_ate_limite(client, db_session):
    """Fallback do horóscopo de hoje → retry; amanhã resolveria sozinho pelo tempo."""
    email = "retry-horo@example.com"
    _setup(client, db_session, email, ["site:plano_lua"])

    for i in range(3):
        r = _generate(client, HORO)
        assert r.status_code == 202, f"Tentativa {i+1}: esperava 202, obteve {r.status_code}"
        # Espera a BackgroundTask rodar (TestClient sincroniza) e consome o result
        client.get("/api/me/readings")

    r4 = _generate(client, HORO)
    assert r4.status_code == 200, f"4ª tentativa: esperava 200 (limite atingido), obteve {r4.status_code}"
