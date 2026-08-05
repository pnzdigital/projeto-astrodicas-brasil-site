"""Preços próprios do mercado AR.

O catálogo argentino nasceu como conversão do brasileiro (BRL × taxa). Três
produtos passaram a ter preço próprio, decidido comercialmente, e não devem
mais acompanhar a conversão. O resto do catálogo continua derivado.
"""

from app import pricing


def _label(product_id: str) -> str:
    return pricing.format_amount(pricing.amount_minor(product_id, "es-AR"), "ARS")


def test_plano_lua_has_its_own_ar_price():
    assert _label("site:plano_lua") == "ARS 9.900"


def test_premium_has_its_own_ar_price():
    assert _label("site:oferta_plano_lua_premium") == "ARS 34.900"


def test_exit_offer_undercuts_the_plan():
    assert _label("site:oferta_plano_lua_exit") == "ARS 6.900"
    lua = pricing.amount_minor("site:plano_lua", "es-AR")
    exit_offer = pricing.amount_minor("site:oferta_plano_lua_exit", "es-AR")
    discount = 1 - exit_offer / lua
    assert 0.28 < discount < 0.32, "a oferta de saída deve ficar perto de 30% off"


def test_brazil_is_untouched_by_the_ar_decision():
    assert pricing.amount_minor("site:plano_lua", "pt-BR") == 2790
    assert pricing.amount_minor("site:oferta_plano_lua_premium", "pt-BR") == 9700
    assert pricing.amount_minor("site:oferta_plano_lua_exit", "pt-BR") == 2090


def test_products_without_an_override_still_convert_from_brl():
    for product_id in ("site:mapa_astral", "site:combo_mapa_astral_amor"):
        expected = pricing.PRICES_BRL_MINOR[product_id] * pricing.BRL_TO_ARS
        assert pricing.amount_minor(product_id, "es-AR") == expected


def test_every_override_points_at_a_real_product():
    for product_id in pricing.PRICES_ARS_MINOR:
        assert product_id in pricing.PRICES_BRL_MINOR, product_id


def test_checkout_amount_follows_the_override():
    assert pricing.amount_units("site:plano_lua", "es-AR") == 9900.0
    assert pricing.amount_units("site:oferta_plano_lua_premium", "es-AR") == 34900.0
