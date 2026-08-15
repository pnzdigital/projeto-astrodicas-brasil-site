"""A lista de leituras não pode entregar o que o gate de abertura recusa.

`POST /api/readings/{content_id}` checa PAID_ONLY_CONTENT: Guia do Mês e
Previsão Semanal exigem Diário Astral PAGO, e trial recebe 403. Mas
`GET /api/me/readings` devolvia todas as linhas do usuário sem checar nada —
com `body_html` e `sections` completos no JSON.

Caso real (15/08/2026): a conta noelia.empt@gmail.com, com entitlement
`source='trial'`, tinha leituras de guia_do_mes e previsao_semanal no
histórico e as lia inteiras pelo portal. O gate do POST não adianta se a
listagem entrega o mesmo texto de graça.

Contrato coberto aqui:
- trial NÃO vê guia_do_mes/previsao_semanal na listagem
- assinante pago VÊ os dois
- o horóscopo diário (liberado no trial) continua visível para o trial
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app.models import Entitlement, Reading
from conftest import create_user, register

PAID_ONLY = ["site:content:guia_do_mes", "site:content:previsao_semanal"]


def _seed(client, email: str, source: str) -> str:
    assert register(client, email, "senha-segura").status_code == 200
    user = create_user(email, "senha-segura")
    with SessionLocal() as db:
        db.add(Entitlement(
            user_id=user.id,
            product_id="site:diario_astral",
            source=source,
            status="available",
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        ))
        for content_id in PAID_ONLY + ["site:content:horoscopo_diario"]:
            db.add(Reading(
                user_id=user.id,
                content_id=content_id,
                product_id="site:diario_astral",
                status="ready",
                title=content_id,
                body_html="<p>texto pago</p>",
            ))
        db.commit()
    return user.id


def _listed(client) -> set[str]:
    r = client.get("/api/me/readings")
    assert r.status_code == 200
    return {item["content_id"] for item in r.json()["readings"]}


def test_trial_nao_ve_conteudo_pago_na_listagem(client):
    _seed(client, "trial-listagem@example.com", "trial")

    listed = _listed(client)

    assert not (set(PAID_ONLY) & listed), (
        "trial não pode receber Guia do Mês nem Previsão Semanal na listagem — "
        f"o JSON traz body_html completo; veio: {listed}"
    )
    assert "site:content:horoscopo_diario" in listed, (
        "o horóscopo diário é justamente o que o trial compra; não pode sumir"
    )


def test_pago_ve_conteudo_pago_na_listagem(client):
    _seed(client, "pago-listagem@example.com", "purchase")

    listed = _listed(client)

    for content_id in PAID_ONLY:
        assert content_id in listed, f"assinante pago precisa ver {content_id}; veio: {listed}"
