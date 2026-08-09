"""Preços próprios do mercado AR.

O catálogo argentino nasceu como conversão do brasileiro (BRL × taxa). Três
produtos passaram a ter preço próprio, decidido comercialmente, e não devem
mais acompanhar a conversão. O resto do catálogo continua derivado.
"""

from app import pricing


def _label(product_id: str) -> str:
    return pricing.format_amount(pricing.amount_minor(product_id, "es-AR"), "ARS")


def test_diario_astral_has_its_own_ar_price():
    assert _label("site:diario_astral") == "ARS 9.900"


def test_premium_has_its_own_ar_price():
    assert _label("site:diario_astral_completo") == "ARS 34.900"


def test_exit_offer_undercuts_the_plan():
    assert _label("site:diario_astral_oferta_saida") == "ARS 8.900"
    lua = pricing.amount_minor("site:diario_astral", "es-AR")
    exit_offer = pricing.amount_minor("site:diario_astral_oferta_saida", "es-AR")
    assert exit_offer < lua, "uma oferta de saída mais cara que o plano não é oferta"


def test_brazil_is_untouched_by_the_ar_decision():
    assert pricing.amount_minor("site:diario_astral", "pt-BR") == 2790
    assert pricing.amount_minor("site:diario_astral_completo", "pt-BR") == 9700
    assert pricing.amount_minor("site:diario_astral_oferta_saida", "pt-BR") == 2090


def test_products_without_an_override_still_convert_from_brl():
    for product_id in ("site:mapa_astral", "site:combo_mapa_astral_amor"):
        expected = pricing.PRICES_BRL_MINOR[product_id] * pricing.BRL_TO_ARS
        assert pricing.amount_minor(product_id, "es-AR") == expected


def test_every_override_points_at_a_real_product():
    for product_id in pricing.PRICES_ARS_MINOR:
        assert product_id in pricing.PRICES_BRL_MINOR, product_id


def test_checkout_amount_follows_the_override():
    assert pricing.amount_units("site:diario_astral", "es-AR") == 9900.0
    assert pricing.amount_units("site:diario_astral_completo", "es-AR") == 34900.0
