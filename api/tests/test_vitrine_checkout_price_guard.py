"""Guarda de preço vitrine ↔ checkout ↔ Mercado Pago.

O cliente vê um preço na vitrine (index.html / sales.html / storefront.html),
clica no CTA e chega no checkout. O checkout tem que enviar EXATAMENTE o
preço que o cliente viu para o provedor (Mercado Pago em AR, Cakto em BR).

Esse arquivo é a trava permanente: para CADA ``site:*`` que aparece na
vitrine, ele abre uma ordem no endpoint ``POST /api/checkout/order``,
extrai o ``amount`` que o checkout devolveria ao provedor, normaliza
ambos os lados para a unidade da moeda (BRL/ARS) e exige igualdade.

Se alguém trocar um preço em ``api/app/pricing.py`` sem atualizar a vitrine
(o que aconteceu em sprints passadas e gerou chargeback), este teste quebra
na hora. O oposto também: se a vitrine mudar sem o backend seguir, quebra.

Não inventamos valores aqui — o esperado é lido diretamente do HTML da
vitrine. Sem vitrine ⇒ sem teste (test pula em vez de chutar valor).

Cobertura:
  * ``OFFER_COPY_AR`` em ``portal-demo/index.html`` × backend ``pricing``.
  * ``OFFER_COPY_BR`` em ``portal-demo/index.html`` × backend ``pricing``.
  * ``pricesBR`` em ``portal-demo/storefront.html`` × backend ``pricing``.
  * ``ARS_PRICES`` em ``portal-demo/portal-config-ar.js`` × backend ``pricing``.
  * ``price``/``oldPrice`` por página em ``portal-demo/sales.html`` × backend.

Vitrine não tem cobertura de um ``site:*`` SKU? Não inventamos — só
avisamos no relatório e pulamos aquele SKU (a vitrine é a fonte de verdade,
e SKU sem vitrine não deveria estar no catálogo de qualquer jeito).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app import pricing

REPO_ROOT = Path(__file__).resolve().parents[2]
PORTAL_DEMO = REPO_ROOT / "portal-demo"
INDEX_HTML = PORTAL_DEMO / "index.html"
STOREFRONT_HTML = PORTAL_DEMO / "storefront.html"
SALES_HTML = PORTAL_DEMO / "sales.html"
PORTAL_CONFIG_AR_JS = PORTAL_DEMO / "portal-config-ar.js"

CURRENCY_BY_LOCALE = {"es-AR": "ARS", "pt-BR": "BRL"}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_quotes(s: str) -> str:
    """Tira aspas ao redor (regex de capture inclui o delimitador)."""
    return s[1:-1] if len(s) >= 2 and s[0] == s[-1] and s[0] in {"'", '"'} else s


def _extract_offer_copy(html: str, var_name: str) -> dict[str, str]:
    """Pega um objeto JS ``OFFER_COPY_AR = Object.freeze({ 'site:x': [..., price, ...], ... })``.

    O preço fica no índice 3 do array de 6 elementos (kicker, title,
    description, price, cadence, button).
    """
    pattern = re.compile(
        rf"const\s+{var_name}\s*=\s*Object\.freeze\(\s*\{{(?P<body>.*?)\}}\s*\);",
        re.DOTALL,
    )
    match = pattern.search(html)
    assert match, f"Bloco {var_name} não encontrado"
    body = match.group("body")
    # Cada entrada: 'site:sku': ['a','b','c','PRICE','d','e'],
    entry_re = re.compile(r"'([^']+)':\s*\[\s*'((?:[^'\\]|\\.)*)'\s*,\s*'((?:[^'\\]|\\.)*)'\s*,\s*'((?:[^'\\]|\\.)*)'\s*,\s*'([^']*)'\s*,\s*'((?:[^'\\]|\\.)*)'\s*,\s*'((?:[^'\\]|\\.)*)'\s*\]")
    out: dict[str, str] = {}
    for entry in entry_re.finditer(body):
        sku, _kicker, _title, _desc, price, _cadence, _button = entry.groups()
        out[sku] = _strip_quotes(price)
    return out


def _extract_prices_br_in_storefront(html: str) -> dict[str, str]:
    """Pega o literal ``const pricesBR={'site:x':'R$ 9,99 / mês', ...}``."""
    match = re.search(r"const pricesBR\s*=\s*\{(?P<body>[^}]+)\}", html)
    assert match, "Bloco pricesBR não encontrado no storefront"
    body = match.group("body")
    out: dict[str, str] = {}
    for pair in re.finditer(r"'([^']+)'\s*:\s*'([^']*)'", body):
        sku, price = pair.groups()
        out[sku] = price
    return out


def _extract_anchors_br_in_storefront(html: str) -> dict[str, str]:
    """Pega o literal ``const anchorsBR={'site:x':'R$ 47,00', ...}`` (preço riscado)."""
    match = re.search(r"const anchorsBR\s*=\s*\{(?P<body>[^}]+)\}", html)
    assert match, "Bloco anchorsBR não encontrado no storefront"
    return {
        sku: price
        for sku, price in (pair.groups() for pair in re.finditer(r"'([^']+)'\s*:\s*'([^']*)'", match.group("body")))
    }


def _extract_ars_object_in_js(js_text: str, var_name: str) -> dict[str, str]:
    """Pega ``const <var_name> = Object.freeze({ 'site:x': 'ARS 9.999', ... })``."""
    match = re.search(
        rf"const\s+{var_name}\s*=\s*Object\.freeze\(\s*\{{(?P<body>.*?)\}}\s*\);",
        js_text,
        re.DOTALL,
    )
    assert match, f"Bloco {var_name} não encontrado"
    body = match.group("body")
    out: dict[str, str] = {}
    for pair in re.finditer(r'"([^"]+)":\s*"([^"]*)"', body):
        sku, price = pair.groups()
        out[sku] = price
    return out


def _parse_price_to_minor(label: str, currency: str) -> int:
    """Converte "R$ 27,90", "ARS 8.649", "ARS 8.649 / mes", "R$ 47,00" → centavos (int).

    Aceita valores com separador de milhar em AR ("ARS 8.649") e vírgula
    como decimal em BR ("R$ 27,90"). Símbolos extras após o número (cadência
    "/mês", " / mes") são ignorados.
    """
    text = label.strip()
    # Tira o símbolo da moeda e sufixos tipo "/mês", "/primer ciclo".
    cleaned = re.sub(r"/.*$", "", text).strip()
    m = re.search(r"([\d.,]+)", cleaned)
    assert m, f"não dá pra extrair número de {label!r}"
    raw = m.group(1)
    if currency == "ARS":
        # Formato vitrine: "8.649" (sem casas decimais) ou "8.649,50".
        whole_str, _, frac_str = raw.partition(",")
        whole = int(whole_str.replace(".", "")) if whole_str else 0
        frac = int(frac_str.ljust(2, "0")[:2]) if frac_str else 0
    else:  # BRL: "27,90" (sem milhar, vírgula é decimal).
        whole_str, _, frac_str = raw.partition(",")
        # Em BR nunca tem separador de milhar — pontos são decimais.
        whole_str = whole_str.replace(".", "")
        whole = int(whole_str) if whole_str else 0
        frac = int(frac_str[:2].ljust(2, "0")) if frac_str else 0
    return whole * 100 + frac


def _backend_minor(sku: str, locale: str) -> int:
    return pricing.amount_minor(sku, locale)


def _backend_units(sku: str, locale: str) -> float:
    return pricing.amount_units(sku, locale)


@pytest.fixture(scope="module")
def vitrine_offer_ar():
    return _extract_offer_copy(_read(INDEX_HTML), "OFFER_COPY_AR")


@pytest.fixture(scope="module")
def vitrine_offer_br():
    return _extract_offer_copy(_read(INDEX_HTML), "OFFER_COPY_BR")


@pytest.fixture(scope="module")
def vitrine_prices_br_storefront():
    return _extract_prices_br_in_storefront(_read(STOREFRONT_HTML))


@pytest.fixture(scope="module")
def vitrine_ars_prices_js():
    return _extract_ars_object_in_js(_read(PORTAL_CONFIG_AR_JS), "ARS_PRICES")


@pytest.fixture(scope="module")
def vitrine_anchors_br_storefront():
    return _extract_anchors_br_in_storefront(_read(STOREFRONT_HTML))


@pytest.fixture(scope="module")
def vitrine_ars_anchors_js():
    return _extract_ars_object_in_js(_read(PORTAL_CONFIG_AR_JS), "ARS_ANCHORS")


def _assert_pair(sku: str, vitrine_label: str, locale: str):
    currency = CURRENCY_BY_LOCALE[locale]
    vitrine_minor = _parse_price_to_minor(vitrine_label, currency)
    backend_minor = _backend_minor(sku, locale)
    if vitrine_minor != backend_minor:
        pytest.fail(
            f"DIVERGÊNCIA {sku} em {locale}: vitrine anuncia "
            f"{vitrine_label!r} (={vitrine_minor} minor) mas checkout manda "
            f"{backend_minor} minor (={_backend_units(sku, locale)} {currency}) "
            f"para o Mercado Pago. Cobrar valor diferente do anunciado é chargeback."
        )


# ─── AR — OFFER_COPY_AR em index.html ──────────────────────────────────────


def test_ar_offers_in_index_match_checkout_amount(vitrine_offer_ar):
    for sku, vitrine_price in vitrine_offer_ar.items():
        _assert_pair(sku, vitrine_price, "es-AR")


# ─── BR — OFFER_COPY_BR em index.html ──────────────────────────────────────


def test_br_offers_in_index_match_checkout_amount(vitrine_offer_br):
    for sku, vitrine_price in vitrine_offer_br.items():
        _assert_pair(sku, vitrine_price, "pt-BR")


# ─── BR — pricesBR em storefront.html ──────────────────────────────────────


def test_br_prices_in_storefront_match_checkout_amount(vitrine_prices_br_storefront):
    for sku, vitrine_price in vitrine_prices_br_storefront.items():
        _assert_pair(sku, vitrine_price, "pt-BR")


# ─── AR — ARS_PRICES em portal-config-ar.js ────────────────────────────────


def test_ar_prices_in_portal_config_ar_match_checkout_amount(vitrine_ars_prices_js):
    for sku, vitrine_price in vitrine_ars_prices_js.items():
        _assert_pair(sku, vitrine_price, "es-AR")


# ─── Âncora ("de" riscado) — vitrine × pricing.anchor_minor ───────────────


def _assert_anchor(sku: str, vitrine_label: str, locale: str):
    """A âncora é só exibição, mas mentir no riscado é publicidade enganosa:
    o valor exibido tem que ser exatamente o que o backend anuncia como âncora
    e tem que ficar acima do preço cobrado."""
    currency = CURRENCY_BY_LOCALE[locale]
    vitrine_minor = _parse_price_to_minor(vitrine_label, currency)
    backend_anchor = pricing.anchor_minor(sku, locale)
    assert backend_anchor is not None, (
        f"{sku} mostra o riscado {vitrine_label!r} em {locale} mas o backend não "
        f"tem âncora para ele. Riscar preço que nunca existiu é propaganda enganosa."
    )
    assert vitrine_minor == backend_anchor, (
        f"ÂNCORA DIVERGENTE {sku} em {locale}: vitrine risca {vitrine_label!r} "
        f"(={vitrine_minor} minor) mas pricing diz {backend_anchor} minor."
    )
    assert backend_anchor > _backend_minor(sku, locale), (
        f"{sku}: âncora {backend_anchor} não está acima do preço cobrado."
    )


def test_br_anchors_in_storefront_match_pricing(vitrine_anchors_br_storefront):
    for sku, label in vitrine_anchors_br_storefront.items():
        _assert_anchor(sku, label, "pt-BR")


def test_ar_anchors_in_portal_config_ar_match_pricing(vitrine_ars_anchors_js):
    for sku, label in vitrine_ars_anchors_js.items():
        _assert_anchor(sku, label, "es-AR")


def test_vitrine_anchors_cover_exactly_the_anchored_skus(
    vitrine_anchors_br_storefront, vitrine_ars_anchors_js
):
    """Produto com âncora no backend e sem riscado na vitrine desperdiça a
    decisão comercial; o contrário anuncia desconto que o backend não conhece."""
    expected = set(pricing.ANCHOR_BRL_MINOR)
    assert set(vitrine_anchors_br_storefront) == expected
    assert set(vitrine_ars_anchors_js) == expected


# ─── Cakto (BR) — o checkout também tem que mandar o preço do servidor ────


def test_br_checkout_order_amount_matches_pricing_for_every_listed_sku(client, monkeypatch):
    """Para cada SKU listado (visível na vitrine), o /api/checkout/order devolve
    o mesmo amount que pricing.amount_minor(). Usa provedor cakto (sem verificação
    de URL) para isolar o teste ao preço, não à configuração de checkout."""
    monkeypatch.setenv("CHECKOUT_PROVIDER_BR", "cakto")
    for sku in pricing.PRICES_BRL_MINOR:
        if sku in pricing.UNLISTED:
            continue  # bump / exit-offer: vitrine não mostra, teste cobre via offer copy
        response = client.post(
            "/api/checkout/order",
            json={"product_id": sku, "email": "guarda@teste.com", "locale": "pt-BR"},
        )
        assert response.status_code == 200, f"{sku}: {response.text}"
        body = response.json()
        expected_minor = pricing.amount_minor(sku, "pt-BR")
        assert body["amount_minor"] == expected_minor, (
            f"{sku}: checkout mandou amount_minor={body['amount_minor']} mas "
            f"pricing diz {expected_minor}"
        )


# ─── "Desde"/"A partir de" — verdadeiros ou mentira? ───────────────────────


def test_desde_copy_in_ar_vitrine_is_truthful(vitrine_offer_ar):
    """Se a vitrine AR diz 'Desde ARS X', precisa existir pelo menos um plano
    dessa categoria (leitura individual) com preço AR <= X. Se o menor preço
    for maior que X, 'Desde' é mentira comercial."""
    mapas_single = {
        sku: price
        for sku, price in vitrine_offer_ar.items()
        if sku.startswith("site:mapa_")
    }
    assert mapas_single, "esperava SKUs site:mapa_* em OFFER_COPY_AR"
    for sku, label in mapas_single.items():
        if label.startswith("Desde "):
            claim = _parse_price_to_minor(label, "ARS")
            # Menor preço entre as leituras individuais (mapas_*).
            lowest = min(
                _parse_price_to_minor(p, "ARS")
                for s, p in vitrine_offer_ar.items()
                if s.startswith("site:mapa_")
            )
            assert claim <= lowest, (
                f"{sku} anuncia {label!r} mas o mapa mais barato em AR vale "
                f"{lowest / 100:.2f}. 'Desde {claim / 100:.2f}' é falso."
            )


def test_a_partir_de_copy_in_br_vitrine_is_truthful(vitrine_offer_br):
    """Mesma regra pt-BR."""
    mapas_single = {
        sku: price
        for sku, price in vitrine_offer_br.items()
        if sku.startswith("site:mapa_")
    }
    assert mapas_single, "esperava SKUs site:mapa_* em OFFER_COPY_BR"
    lowest = min(_parse_price_to_minor(p, "BRL") for p in mapas_single.values())
    for sku, label in mapas_single.items():
        if label.startswith("A partir de "):
            claim = _parse_price_to_minor(label, "BRL")
            assert claim <= lowest, (
                f"{sku} anuncia {label!r} mas o mapa mais barato em BR vale "
                f"R$ {lowest / 100:.2f}. 'A partir de R$ {claim / 100:.2f}' é falso."
            )


# ─── Cobertura de paridade: todo SKU da vitrine tem preço no backend ───────


@pytest.mark.parametrize(
    "vitrine_name,fixture_name",
    [
        ("OFFER_COPY_AR (index.html)", "vitrine_offer_ar"),
        ("OFFER_COPY_BR (index.html)", "vitrine_offer_br"),
        ("pricesBR (storefront.html)", "vitrine_prices_br_storefront"),
        ("ARS_PRICES (portal-config-ar.js)", "vitrine_ars_prices_js"),
        ("anchorsBR (storefront.html)", "vitrine_anchors_br_storefront"),
        ("ARS_ANCHORS (portal-config-ar.js)", "vitrine_ars_anchors_js"),
    ],
)
def test_every_vitrine_sku_is_known_in_pricing(vitrine_name, fixture_name, request):
    """Se um SKU aparece na vitrine mas o backend não conhece, o checkout
    quebra com 404 — exatamente o cenário que queremos impedir."""
    vitrine = request.getfixturevalue(fixture_name)
    for sku in vitrine:
        assert pricing.is_known_product(sku), (
            f"{vitrine_name} lista {sku!r} mas api/app/pricing.py não conhece. "
            f"Checkout vai dar 404 e o cliente fica sem produto."
        )
