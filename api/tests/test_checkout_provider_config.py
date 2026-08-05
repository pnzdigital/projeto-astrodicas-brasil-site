"""Quem cobra em cada mercado é configuração, não regra fixa no código.

O padrão continua sendo Argentina no Mercado Pago e Brasil na Cakto: sem
variável de ambiente, nada muda. Trocar o Brasil para o Mercado Pago de
verdade ainda depende de uma conta brasileira (MLB) — contas do Mercado Pago
são presas ao país e a conta argentina em uso não cobra em BRL.
"""

import pytest

from app import checkout


@pytest.fixture(autouse=True)
def _sem_configuracao(monkeypatch):
    monkeypatch.delenv("CHECKOUT_PROVIDER_AR", raising=False)
    monkeypatch.delenv("CHECKOUT_PROVIDER_BR", raising=False)


@pytest.fixture()
def _mp_habilitado(monkeypatch):
    monkeypatch.setenv("MP_ACCESS_TOKEN", "TEST-token")
    monkeypatch.setenv("MP_PUBLIC_KEY", "APP_USR-0000aaaa-1111-2222-3333-444455556666")
    monkeypatch.setenv("SITE_PUBLIC_URL", "https://astrodicas.example")


def test_padrao_preserva_o_comportamento_de_sempre():
    assert checkout.provider_for("es-AR") == "mercadopago"
    assert checkout.provider_for("pt-BR") == "cakto"


def test_brasil_pode_ser_configurado_no_mercado_pago(monkeypatch):
    monkeypatch.setenv("CHECKOUT_PROVIDER_BR", "mercadopago")
    assert checkout.provider_for("pt-BR") == "mercadopago"
    assert checkout.provider_for("es-AR") == "mercadopago", "AR não muda junto"


def test_argentina_pode_sair_do_mercado_pago(monkeypatch):
    monkeypatch.setenv("CHECKOUT_PROVIDER_AR", "cakto")
    assert checkout.provider_for("es-AR") == "cakto"
    assert checkout.provider_for("pt-BR") == "cakto"


def test_valor_desconhecido_nao_derruba_a_venda(monkeypatch, caplog):
    monkeypatch.setenv("CHECKOUT_PROVIDER_BR", "pagseguro")
    assert checkout.provider_for("pt-BR") == "cakto", "typo cai no padrão em vez de quebrar"
    assert "pagseguro" in caplog.text


def test_maiuscula_e_espaco_nao_atrapalham(monkeypatch):
    monkeypatch.setenv("CHECKOUT_PROVIDER_BR", "  MercadoPago  ".replace("MercadoPago", "MERCADOPAGO"))
    assert checkout.provider_for("pt-BR") == "mercadopago"


def test_catalogo_br_segue_a_configuracao(client, monkeypatch, _mp_habilitado):
    padrao = client.get("/api/catalog?locale=pt-BR").json()["checkout"]
    assert padrao["provider"] == "cakto"
    assert padrao["transparent"] is False
    assert padrao["public_key"] == ""

    monkeypatch.setenv("CHECKOUT_PROVIDER_BR", "mercadopago")
    configurado = client.get("/api/catalog?locale=pt-BR").json()["checkout"]
    assert configurado["provider"] == "mercadopago"
    assert configurado["transparent"] is True, "checkout transparente segue o provedor, não o país"
    assert configurado["public_key"].startswith("APP_USR-")


def test_pedido_br_configurado_no_mp_nasce_com_o_provedor_certo(client, monkeypatch, _mp_habilitado):
    monkeypatch.setenv("CHECKOUT_PROVIDER_BR", "mercadopago")
    pedido = client.post(
        "/api/checkout/order",
        json={"product_id": "site:mapa_astral", "email": "br@cliente.com", "name": "Cliente BR", "locale": "pt-BR"},
    )
    assert pedido.status_code == 200, pedido.text
    assert pedido.json()["currency"] == "BRL", "o preço continua sendo do mercado, não do provedor"


def test_pedido_da_cakto_nao_pode_ser_cobrado_pela_rota_do_mercado_pago(client, _mp_habilitado):
    """O pedido diz quem cobra. Sem isso, uma venda BR cairia na conta argentina."""
    pedido = client.post(
        "/api/checkout/order",
        json={"product_id": "site:mapa_astral", "email": "br@cliente.com", "name": "Cliente BR", "locale": "pt-BR"},
    ).json()

    resposta = client.post(
        "/api/checkout/payment",
        json={
            "order_id": pedido["order_id"],
            "form_data": {
                "payment_method_id": "visa",
                "token": "tok",
                "installments": 1,
                "payer": {"email": "br@cliente.com"},
            },
        },
    )
    assert resposta.status_code == 409, resposta.text
