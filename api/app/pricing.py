"""Preços canônicos do canal site.

Fonte única de verdade de preço. O front (storefront, páginas de venda e
checkout) nunca decide valor: ele só exibe o que este módulo devolve. O
Mercado Pago recebe o valor calculado aqui, nunca o valor enviado pelo
cliente.

Mercado ``BR`` cobra em BRL; mercado ``AR`` cobra em ARS pela conta argentina.
A maioria dos produtos argentinos continua derivada do preço brasileiro pela
mesma taxa (BRL_TO_ARS), de modo que qualquer desconto percentual da página
brasileira continue valendo igual na página argentina. Alguns produtos
listados em PRICES_ARS_MINOR fogem da conversão: a decisão comercial fixou
um preço próprio em ARS para eles, e amount_minor() consulta o override
antes de cair na taxa.
"""

from __future__ import annotations

BRL_TO_ARS = 310  # taxa de referência entre catálogos; valores próprios em PRICES_ARS_MINOR escapam dela.

# product_id -> preço em centavos de BRL.
PRICES_BRL_MINOR: dict[str, int] = {
    "site:plano_lua": 2790,
    "site:mapa_astral": 4700,
    "site:mapa_amor_sinastria": 4700,
    "site:mapa_carreira": 4700,
    "site:mapa_prosperidade": 4700,
    "site:oferta_plano_lua_premium": 9700,
    "site:combo_mapa_astral_amor": 7900,
    "site:combo_mapa_astral_carreira": 7900,
    "site:combo_mapa_astral_prosperidade": 7900,
    "site:combo_amor_carreira": 7900,
    "site:combo_amor_prosperidade": 7900,
    "site:combo_carreira_prosperidade": 7900,
    "site:combo_plano_lua_mapa_astral": 6700,
    "site:combo_plano_lua_mapa_amor": 6700,
    "site:combo_plano_lua_mapa_prosperidade": 6700,
    # Ofertas de desconto da página V2: order bump do Completo e oferta de saída
    # do Círculo da Lua. Entram na conversão argentina pela mesma taxa, então o
    # percentual de desconto é idêntico nos dois mercados.
    "site:oferta_plano_lua_premium_bump": 5790,
    "site:oferta_plano_lua_exit": 2090,
}

# product_id -> preço em centavos de ARS.
# Estes preços são fixos em reais argentinos: não acompanham BRL_TO_ARS.
# Tudo o que não estiver aqui continua sendo convertido pela taxa.
PRICES_ARS_MINOR: dict[str, int] = {
    "site:plano_lua": 990000,                # ARS 9.900/mês
    "site:oferta_plano_lua_premium": 3490000,  # ARS 34.900 pagamento único
    "site:oferta_plano_lua_exit": 890000,    # ARS 8.900, abaixo do Círculo da Lua
}

PRODUCT_TITLES: dict[str, dict[str, str]] = {
    "site:plano_lua": {"pt-BR": "Círculo da Lua", "es-AR": "Círculo de la Luna"},
    "site:mapa_astral": {"pt-BR": "Mapa Astral Completo", "es-AR": "Mapa Astral Completo"},
    "site:mapa_amor_sinastria": {"pt-BR": "Mapa do Amor / Sinastria", "es-AR": "Mapa del Amor / Sinastría"},
    "site:mapa_carreira": {"pt-BR": "Mapa da Carreira", "es-AR": "Mapa de la Carrera"},
    "site:mapa_prosperidade": {"pt-BR": "Mapa da Prosperidade", "es-AR": "Mapa de la Prosperidad"},
    "site:oferta_plano_lua_premium": {"pt-BR": "Círculo Completo", "es-AR": "Círculo Completo"},
    "site:combo_mapa_astral_amor": {"pt-BR": "Mapa Astral + Mapa do Amor", "es-AR": "Mapa Astral + Mapa del Amor"},
    "site:combo_mapa_astral_carreira": {"pt-BR": "Mapa Astral + Mapa da Carreira", "es-AR": "Mapa Astral + Mapa de la Carrera"},
    "site:combo_mapa_astral_prosperidade": {"pt-BR": "Mapa Astral + Mapa da Prosperidade", "es-AR": "Mapa Astral + Mapa de la Prosperidad"},
    "site:combo_amor_carreira": {"pt-BR": "Mapa do Amor + Mapa da Carreira", "es-AR": "Mapa del Amor + Mapa de la Carrera"},
    "site:combo_amor_prosperidade": {"pt-BR": "Mapa do Amor + Mapa da Prosperidade", "es-AR": "Mapa del Amor + Mapa de la Prosperidad"},
    "site:combo_carreira_prosperidade": {"pt-BR": "Mapa da Carreira + Mapa da Prosperidade", "es-AR": "Mapa de la Carrera + Mapa de la Prosperidad"},
    "site:combo_plano_lua_mapa_astral": {"pt-BR": "Círculo da Lua + Mapa Astral", "es-AR": "Círculo de la Luna + Mapa Astral"},
    "site:combo_plano_lua_mapa_amor": {"pt-BR": "Círculo da Lua + Mapa do Amor", "es-AR": "Círculo de la Luna + Mapa del Amor"},
    "site:combo_plano_lua_mapa_prosperidade": {"pt-BR": "Círculo da Lua + Mapa da Prosperidade", "es-AR": "Círculo de la Luna + Mapa de la Prosperidad"},
    "site:oferta_plano_lua_premium_bump": {"pt-BR": "Círculo Completo (oferta)", "es-AR": "Círculo Completo (oferta)"},
    "site:oferta_plano_lua_exit": {"pt-BR": "Círculo da Lua (oferta especial)", "es-AR": "Círculo de la Luna (oferta especial)"},
}

# Produtos entregues por um combo/oferta. A liberação concede também os itens.
BUNDLES: dict[str, tuple[str, ...]] = {
    # As ofertas com desconto liberam exatamente o mesmo acesso do produto cheio.
    "site:oferta_plano_lua_premium_bump": (
        "site:oferta_plano_lua_premium",
        "site:plano_lua",
        "site:mapa_astral",
        "site:mapa_amor_sinastria",
        "site:mapa_prosperidade",
    ),
    # Oferta de saída / winback: pagamento avulso, sem trial — brinde incluso.
    "site:oferta_plano_lua_exit": ("site:plano_lua", "site:mapa_astral"),
    "site:oferta_plano_lua_premium": (
        "site:plano_lua",
        "site:mapa_astral",
        "site:mapa_amor_sinastria",
        "site:mapa_prosperidade",
    ),
    # Brinde do 1º mês: mapa_astral concedido após a primeira cobrança confirmada.
    # sync_entitlements (subscriptions.py) garante que bundle items não saem durante o trial.
    "site:plano_lua": ("site:mapa_astral",),
    "site:combo_mapa_astral_amor": ("site:mapa_astral", "site:mapa_amor_sinastria"),
    "site:combo_mapa_astral_carreira": ("site:mapa_astral", "site:mapa_carreira"),
    "site:combo_mapa_astral_prosperidade": ("site:mapa_astral", "site:mapa_prosperidade"),
    "site:combo_amor_carreira": ("site:mapa_amor_sinastria", "site:mapa_carreira"),
    "site:combo_amor_prosperidade": ("site:mapa_amor_sinastria", "site:mapa_prosperidade"),
    "site:combo_carreira_prosperidade": ("site:mapa_carreira", "site:mapa_prosperidade"),
    "site:combo_plano_lua_mapa_astral": ("site:plano_lua", "site:mapa_astral"),
    "site:combo_plano_lua_mapa_amor": ("site:plano_lua", "site:mapa_amor_sinastria"),
    "site:combo_plano_lua_mapa_prosperidade": ("site:plano_lua", "site:mapa_prosperidade"),
}

RECURRING = {"site:plano_lua", "site:oferta_plano_lua_exit"}

# Ofertas que só existem dentro da página de vendas (order bump e saída).
# Continuam no catálogo da API porque o checkout precisa do preço, mas a
# vitrine não deve listá-las como produto normal.
UNLISTED = {"site:oferta_plano_lua_premium_bump", "site:oferta_plano_lua_exit"}

MARKETS = {"pt-BR": "BR", "es-AR": "AR"}
CURRENCIES = {"BR": "BRL", "AR": "ARS"}


def normalize_locale(locale: str | None) -> str:
    return "es-AR" if (locale or "").lower().startswith("es") else "pt-BR"


def market_for(locale: str | None) -> str:
    return MARKETS[normalize_locale(locale)]


def currency_for(locale: str | None) -> str:
    return CURRENCIES[market_for(locale)]


def is_known_product(product_id: str) -> bool:
    return product_id in PRICES_BRL_MINOR


def amount_minor(product_id: str, locale: str | None) -> int:
    """Preço em centavos da moeda do mercado."""
    base = PRICES_BRL_MINOR[product_id]
    if market_for(locale) == "AR":
        override = PRICES_ARS_MINOR.get(product_id)
        return override if override is not None else base * BRL_TO_ARS
    return base


def amount_units(product_id: str, locale: str | None) -> float:
    """Preço na unidade da moeda, formato aceito pelo Mercado Pago."""
    return round(amount_minor(product_id, locale) / 100, 2)


def title_for(product_id: str, locale: str | None) -> str:
    titles = PRODUCT_TITLES.get(product_id, {})
    return titles.get(normalize_locale(locale)) or titles.get("pt-BR") or product_id


def granted_products(product_id: str) -> tuple[str, ...]:
    """Produto comprado mais tudo que ele libera."""
    return (product_id, *BUNDLES.get(product_id, ()))


def format_amount(minor: int, currency: str) -> str:
    """Formata o valor no padrão da moeda; ``BRL`` cai no ramo padrão pt-BR."""
    whole, cents = divmod(abs(minor), 100)
    grouped = f"{whole:,}".replace(",", ".")
    if currency == "ARS":
        return f"ARS {grouped}" if cents == 0 else f"ARS {grouped},{cents:02d}"
    return f"R$ {grouped},{cents:02d}"


def catalog(locale: str | None) -> list[dict]:
    locale = normalize_locale(locale)
    currency = currency_for(locale)
    rows = []
    for product_id in PRICES_BRL_MINOR:
        minor = amount_minor(product_id, locale)
        rows.append(
            {
                "product_id": product_id,
                "title": title_for(product_id, locale),
                "currency": currency,
                "amount_minor": minor,
                "amount": round(minor / 100, 2),
                "price_label": format_amount(minor, currency),
                "recurring": product_id in RECURRING,
                "listed": product_id not in UNLISTED,
            }
        )
    return rows
