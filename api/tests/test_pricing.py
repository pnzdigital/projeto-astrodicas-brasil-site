"""Preços canônicos do catálogo: todos os SKUs, BR e AR, e formatação."""

from app import pricing


def test_all_brl_prices_match_readme():
    assert pricing.amount_minor("site:diario_astral", "pt-BR") == 2790  # R$ 27,90/mês
    assert pricing.amount_minor("site:diario_astral_completo", "pt-BR") == 9700  # R$ 97,00 único


def test_all_ars_prices_match_readme():
    assert pricing.format_amount(pricing.amount_minor("site:diario_astral", "es-AR"), "ARS") == "ARS 14.900"
    assert pricing.format_amount(pricing.amount_minor("site:diario_astral_completo", "es-AR"), "ARS") == "ARS 34.900"


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
    assert pricing.is_known_product("site:diario_astral") is True
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
    granted = pricing.granted_products("site:diario_astral_completo")
    assert "site:diario_astral_completo" in granted
    assert "site:diario_astral" in granted
    assert "site:mapa_astral" in granted


def test_granted_products_without_bundle_is_just_the_product():
    assert pricing.granted_products("site:mapa_astral") == ("site:mapa_astral",)


def test_catalog_marks_unlisted_offers():
    rows = pricing.catalog("pt-BR")
    unlisted = {row["product_id"] for row in rows if not row["listed"]}
    assert unlisted == pricing.UNLISTED


# ─── Preço ancorado (de/por) ───────────────────────────────────────────────

ANCHORED_SINGLES = (
    "site:mapa_astral",
    "site:mapa_amor_sinastria",
    "site:mapa_carreira",
    "site:mapa_prosperidade",
)
ANCHORED_COMBOS = (
    "site:combo_mapa_astral_amor",
    "site:combo_mapa_astral_carreira",
    "site:combo_mapa_astral_prosperidade",
    "site:combo_amor_carreira",
    "site:combo_amor_prosperidade",
    "site:combo_carreira_prosperidade",
    "site:combo_diario_astral_mapa_astral",
    "site:combo_diario_astral_mapa_amor",
    "site:combo_diario_astral_mapa_prosperidade",
)


def test_current_prices_after_the_anchor_cut():
    for sku in ANCHORED_SINGLES:
        assert pricing.amount_minor(sku, "pt-BR") == 3490
    for sku in ("site:combo_mapa_astral_amor", "site:combo_carreira_prosperidade"):
        assert pricing.amount_minor(sku, "pt-BR") == 5890
    for sku in ("site:combo_diario_astral_mapa_astral", "site:combo_diario_astral_mapa_amor"):
        assert pricing.amount_minor(sku, "pt-BR") == 4990


def test_anchor_present_on_singles_and_combos():
    for sku in ANCHORED_SINGLES:
        assert pricing.anchor_minor(sku, "pt-BR") == 4700
    for sku in ANCHORED_COMBOS[:6]:
        assert pricing.anchor_minor(sku, "pt-BR") == 7900
    for sku in ANCHORED_COMBOS[6:]:
        assert pricing.anchor_minor(sku, "pt-BR") == 6700


def test_anchor_absent_on_diario_astral_and_the_other_untouched_skus():
    for sku in (
        "site:diario_astral",
        "site:diario_astral_completo",
        "site:diario_astral_completo_bump",
        "site:diario_astral_oferta_saida",
    ):
        assert pricing.anchor_minor(sku, "pt-BR") is None
        assert pricing.anchor_minor(sku, "es-AR") is None


def test_anchor_follows_the_same_market_rule_as_the_price():
    for sku in pricing.ANCHOR_BRL_MINOR:
        assert sku not in pricing.PRICES_ARS_MINOR  # nenhum ancorado tem preço AR próprio hoje
        expected = pricing.ANCHOR_BRL_MINOR[sku] * pricing.BRL_TO_ARS
        assert pricing.anchor_minor(sku, "es-AR") == expected


def test_anchor_is_always_above_the_charged_price():
    for sku in pricing.ANCHOR_BRL_MINOR:
        for locale in ("pt-BR", "es-AR"):
            assert pricing.anchor_minor(sku, locale) > pricing.amount_minor(sku, locale)


def test_anchor_never_leaks_into_the_charged_amount():
    """A âncora é só exibição: nenhum produto pode cobrar o valor riscado."""
    for sku, anchor_brl in pricing.ANCHOR_BRL_MINOR.items():
        assert pricing.amount_minor(sku, "pt-BR") != anchor_brl
        assert pricing.amount_units(sku, "pt-BR") == round(pricing.PRICES_BRL_MINOR[sku] / 100, 2)


def test_catalog_exposes_anchor_fields():
    rows = {row["product_id"]: row for row in pricing.catalog("pt-BR")}
    mapa = rows["site:mapa_astral"]
    assert mapa["anchor_minor"] == 4700
    assert mapa["anchor_label"] == "R$ 47,00"
    assert mapa["amount_minor"] == 3490
    assert mapa["price_label"] == "R$ 34,90"

    diario = rows["site:diario_astral"]
    assert diario["anchor_minor"] is None
    assert diario["anchor_label"] is None
    assert diario["amount_minor"] == 2790


def test_catalog_anchor_label_uses_market_currency():
    rows = {row["product_id"]: row for row in pricing.catalog("es-AR")}
    combo = rows["site:combo_mapa_astral_amor"]
    assert combo["anchor_label"] == "ARS 24.490"
    assert combo["price_label"] == "ARS 18.259"


def test_catalog_anchor_is_absent_for_every_sku_outside_the_anchor_dict():
    for row in pricing.catalog("pt-BR"):
        has_anchor = row["product_id"] in pricing.ANCHOR_BRL_MINOR
        assert (row["anchor_minor"] is not None) is has_anchor
        assert (row["anchor_label"] is not None) is has_anchor


def test_catalog_marks_recurring_products():
    rows = pricing.catalog("pt-BR")
    recurring = {row["product_id"] for row in rows if row["recurring"]}
    assert recurring == pricing.RECURRING
