"""Preços canônicos do catálogo: todos os SKUs, BR e AR, e formatação."""

from app import pricing


def test_all_brl_prices_match_readme():
    assert pricing.amount_minor("site:plano_lua", "pt-BR") == 2790  # R$ 27,90/mês
    assert pricing.amount_minor("site:oferta_plano_lua_premium", "pt-BR") == 9700  # R$ 97,00 único


def test_all_ars_prices_match_readme():
    assert pricing.format_amount(pricing.amount_minor("site:plano_lua", "es-AR"), "ARS") == "ARS 9.900"
    assert pricing.format_amount(pricing.amount_minor("site:oferta_plano_lua_premium", "es-AR"), "ARS") == "ARS 34.900"


def test_every_sku_without_an_override_converts_at_the_same_rate():
    """A conversão continua valendo para todo produto que não tem preço
    argentino próprio; os que têm são verificados em test_pricing_ar_overrides."""
    for product_id, brl_minor in pricing.PRICES_BRL_MINOR.items():
        assert pricing.amount_minor(product_id, "pt-BR") == brl_minor
        if product_id in pricing.PRICES_ARS_MINOR:
            continue
        assert pricing.amount_minor(product_id, "es-AR") == brl_minor * pricing.BRL_TO_ARS


def test_every_sku_has_a_title_in_both_locales():
    for product_id in pricing.PRICES_BRL_MINOR:
        assert pricing.title_for(product_id, "pt-BR")
        assert pricing.title_for(product_id, "es-AR")


def test_title_for_unknown_product_falls_back_to_id():
    assert pricing.title_for("site:nope", "pt-BR") == "site:nope"


def test_is_known_product():
    assert pricing.is_known_product("site:plano_lua") is True
    assert pricing.is_known_product("site:nope") is False


def test_normalize_locale_defaults_to_pt_br():
    assert pricing.normalize_locale(None) == "pt-BR"
    assert pricing.normalize_locale("") == "pt-BR"
    assert pricing.normalize_locale("en-US") == "pt-BR"
    assert pricing.normalize_locale("es-MX") == "es-AR"


def test_format_amount_brl_branch():
    assert pricing.format_amount(2790, "BRL") == "R$ 27,90"
    assert pricing.format_amount(9700, "BRL") == "R$ 97,00"


def test_format_amount_ars_with_cents():
    assert pricing.format_amount(864950, "ARS") == "ARS 8.649,50"


def test_granted_products_bundle_includes_purchased_item():
    granted = pricing.granted_products("site:oferta_plano_lua_premium")
    assert "site:oferta_plano_lua_premium" in granted
    assert "site:plano_lua" in granted
    assert "site:mapa_astral" in granted


def test_granted_products_without_bundle_is_just_the_product():
    assert pricing.granted_products("site:mapa_astral") == ("site:mapa_astral",)


def test_catalog_marks_unlisted_offers():
    rows = pricing.catalog("pt-BR")
    unlisted = {row["product_id"] for row in rows if not row["listed"]}
    assert unlisted == pricing.UNLISTED


def test_catalog_marks_recurring_products():
    rows = pricing.catalog("pt-BR")
    recurring = {row["product_id"] for row in rows if row["recurring"]}
    assert recurring == pricing.RECURRING
