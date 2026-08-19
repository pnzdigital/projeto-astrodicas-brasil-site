"""De onde veio a venda — captura no pedido e relatório no admin.

Antes disto o painel sabia QUANTO vendeu e não sabia POR ONDE: decidir onde
investir em tráfego era chute. Duas origens por pedido, não uma — a primeira
(quem descobriu a cliente) e a última (quem fechou), porque creditar só a última
apaga o anúncio que apresentou a marca a quem voltou pelo Google dois dias
depois.
"""

import json
import os
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.admin import router as admin_router
from app.checkout import normaliza_atribuicao
from app.db import SessionLocal
from app.models import Order

os.environ["ADMIN_PASSWORD"] = "letmein-test"


@pytest.fixture()
def admin(clean_database):
    app = FastAPI()
    app.include_router(admin_router)
    with TestClient(app) as client:
        r = client.post("/api/admin/login", json={"password": "letmein-test"})
        assert r.status_code == 200, r.text
        yield client


def _pedido(*, status="paid", amount=3490, first="", last="", campanha="", moeda="BRL") -> None:
    db = SessionLocal()
    try:
        db.add(Order(
            id=str(uuid4()), provider="ggcheckout", external_id=str(uuid4()),
            product_id="site:mapa_astral", status=status, amount_minor=amount,
            currency=moeda, locale="pt-BR", market="BR",
            first_source=first, last_source=last, last_campaign=campanha,
        ))
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------- normalização

def test_origem_vira_minuscula_para_o_relatorio_nao_quebrar():
    """"Instagram", "instagram " e "instagram" viariam TRÊS linhas no painel —
    e aí o número que decide onde investir está errado."""
    limpo = normaliza_atribuicao({"last_source": "  Instagram  ", "last_medium": "BIO"})
    assert limpo["last_source"] == "instagram"
    assert limpo["last_medium"] == "bio"


def test_url_preserva_caixa_porque_caminho_diferencia():
    limpo = normaliza_atribuicao({"landing_page": "/Diario-Astral-1?utm_source=X"})
    assert limpo["landing_page"] == "/Diario-Astral-1?utm_source=X"


def test_campo_gigante_e_truncado_e_nao_recusado():
    """Link com utm_campaign gigante é erro de quem montou o link. Recusar
    custaria a venda; truncar custa um pedaço do rótulo."""
    limpo = normaliza_atribuicao({"last_campaign": "c" * 500})
    assert len(limpo["last_campaign"]) == 120


def test_lixo_e_ausencia_nao_derrubam_a_compra():
    assert normaliza_atribuicao(None) == {}
    assert normaliza_atribuicao({"last_source": 42, "inventado": "x"}) == {}


# ------------------------------------------------------------------- relatório

def test_relatorio_credita_a_ultima_origem_por_padrao(admin):
    _pedido(first="instagram", last="google")
    linhas = admin.get("/api/admin/attribution").json()["rows"]
    assert [l["source"] for l in linhas] == ["google"]


def test_relatorio_sabe_creditar_a_primeira_origem(admin):
    """A pergunta "quem trouxe essa cliente" é outra, e o painel alterna."""
    _pedido(first="instagram", last="google")
    linhas = admin.get("/api/admin/attribution?janela=first").json()["rows"]
    assert [l["source"] for l in linhas] == ["instagram"]


def test_pedido_sem_marcacao_aparece_como_direto(admin):
    """Vazio não é ausência de dado: é a resposta "veio direto". Esconder isso
    faria o painel parecer mais preciso do que é."""
    _pedido(first="", last="")
    linhas = admin.get("/api/admin/attribution").json()["rows"]
    assert linhas[0]["source"] == "(direto / sem marcação)"


def test_pedido_pendente_conta_visita_mas_nao_faturamento(admin):
    """É essa diferença que vira conversão por canal — o canal que traz muita
    gente e vende pouco precisa ficar visível ao lado do que traz pouca e vende."""
    _pedido(status="paid", last="instagram")
    _pedido(status="pending", last="instagram")
    linha = admin.get("/api/admin/attribution").json()["rows"][0]
    assert linha["orders_count"] == 2
    assert linha["sales_count"] == 1
    assert linha["conversion"] == 0.5
    assert "34,90" in linha["revenue_label"]


def test_ordena_por_venda_e_nao_por_visita(admin):
    """O painel existe para mostrar o que dá dinheiro: canal que só traz
    tráfego não pode encabeçar a tabela."""
    for _ in range(5):
        _pedido(status="pending", last="tiktok")
    _pedido(status="paid", last="instagram")
    linhas = admin.get("/api/admin/attribution").json()["rows"]
    assert linhas[0]["source"] == "instagram"


def test_moedas_diferentes_nao_sao_somadas(admin):
    """BR e AR na mesma linha somariam real com peso e inventariam faturamento."""
    _pedido(last="meta", amount=3490, moeda="BRL")
    _pedido(last="meta", amount=1281900, moeda="ARS")
    linha = admin.get("/api/admin/attribution").json()["rows"][0]
    assert set(linha["revenue_minor_by_currency"]) == {"BRL", "ARS"}
    assert "+" in linha["revenue_label"]


def test_origem_aparece_na_linha_de_cada_venda(admin):
    """Relatório agregado não responde "essa venda específica veio de onde?"."""
    _pedido(first="instagram", last="google", campanha="lancamento-agosto")
    venda = admin.get("/api/admin/sales").json()["sales"][0]
    assert venda["first_source"] == "instagram"
    assert venda["last_source"] == "google"
    assert venda["last_campaign"] == "lancamento-agosto"


@pytest.fixture()
def checkout_br(monkeypatch):
    """Checkout do Brasil precisa da URL do GG configurada, senão devolve 503
    antes de chegar perto da atribuição."""
    monkeypatch.setenv("GG_CHECKOUT_URLS", json.dumps({"site:mapa_astral": "https://checkout.gg.test/mapa"}))


def test_origem_atravessa_o_checkout_ate_o_painel(admin, client, checkout_br):
    """O caminho inteiro: navegador manda a origem no pedido, o pedido guarda,
    o painel agrupa. Cada pedaço tinha teste; o encaixe entre eles não tinha —
    e é no encaixe que atribuição costuma morrer silenciosamente."""
    resposta = client.post("/api/checkout/order", json={
        "product_id": "site:mapa_astral",
        "email": "compradora@example.com",
        "name": "Compradora",
        "locale": "pt-BR",
        "attribution": {
            "first_source": "Instagram", "first_medium": "bio",
            "last_source": "meta", "last_medium": "ads",
            "last_campaign": "lancamento-agosto", "last_content": "video-hook-1",
            "landing_page": "/diario-astral-1?utm_source=meta",
        },
    })
    assert resposta.status_code == 200, resposta.text

    venda = admin.get("/api/admin/sales").json()["sales"][0]
    assert venda["first_source"] == "instagram", "primeira origem tem que sobreviver ao caminho"
    assert venda["last_campaign"] == "lancamento-agosto"
    assert venda["landing_page"] == "/diario-astral-1?utm_source=meta"

    relatorio = admin.get("/api/admin/attribution").json()["rows"]
    assert relatorio[0]["source"] == "meta"
    assert relatorio[0]["campaign"] == "lancamento-agosto"
    # Pendente: pedido aberto e ainda não pago conta visita, não faturamento.
    assert relatorio[0]["orders_count"] == 1
    assert relatorio[0]["sales_count"] == 0


def test_compra_sem_nenhuma_marcacao_continua_funcionando(admin, client, checkout_br):
    """Atribuição é acessório: se o navegador não mandar nada, a venda acontece."""
    resposta = client.post("/api/checkout/order", json={
        "product_id": "site:mapa_astral", "email": "direta@example.com",
        "name": "Direta", "locale": "pt-BR",
    })
    assert resposta.status_code == 200, resposta.text
    assert admin.get("/api/admin/attribution").json()["rows"][0]["source"] == "(direto / sem marcação)"
