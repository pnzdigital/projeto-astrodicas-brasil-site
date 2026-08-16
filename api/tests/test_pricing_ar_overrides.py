"""Preços próprios do mercado AR.

O catálogo argentino nasceu como conversão do brasileiro (BRL × taxa). Três
produtos passaram a ter preço próprio, decidido comercialmente, e não devem
mais acompanhar a conversão. O resto do catálogo continua derivado.
"""

from app import pricing


def _label(product_id: str) -> str:
    return pricing.format_amount(pricing.amount_minor(product_id, "es-AR"), "ARS")


def test_diario_astral_has_its_own_ar_price():
    assert _label("site:diario_astral") == "ARS 14.900"


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
    for product_id in pricing.PRICES_BRL_MINOR:
        if product_id in pricing.PRICES_ARS_MINOR:
            continue
        expected = pricing.PRICES_BRL_MINOR[product_id] * pricing.BRL_TO_ARS
        assert pricing.amount_minor(product_id, "es-AR") == expected


def test_ar_price_hike_kept_the_plan_prices_frozen():
    """Reajuste de 16/08/2026: mapas e combos sobem ARS 2.000, plano não.

    O Diário Astral (e o Completo) ficaram de fora por decisão comercial. Sem
    este teste, um próximo reajuste em massa arrastaria o plano junto sem
    ninguém perceber até a primeira cliente reclamar do preço.
    """
    assert pricing.amount_minor("site:diario_astral", "es-AR") == 1490000
    assert pricing.amount_minor("site:diario_astral_completo", "es-AR") == 3490000
    assert pricing.amount_minor("site:mapa_astral", "es-AR") == 1281900
    assert pricing.amount_minor("site:combo_mapa_astral_amor", "es-AR") == 2025900
    assert pricing.amount_minor("site:combo_diario_astral_mapa_astral", "es-AR") == 1746900
    # O Brasil não sente nada disso.
    assert pricing.amount_minor("site:mapa_astral", "pt-BR") == pricing.PRICES_BRL_MINOR["site:mapa_astral"]


def test_every_override_points_at_a_real_product():
    for product_id in pricing.PRICES_ARS_MINOR:
        assert product_id in pricing.PRICES_BRL_MINOR, product_id


def test_checkout_amount_follows_the_override():
    assert pricing.amount_units("site:diario_astral", "es-AR") == 14900.0
    assert pricing.amount_units("site:diario_astral_completo", "es-AR") == 34900.0
