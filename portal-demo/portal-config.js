/**
 * Configuração do portal web AstroDicas.
 *
 * Este arquivo pertence exclusivamente ao canal SITE. O Telegram é outro
 * produto/sistema: não colocar aqui tokens, webhooks, preços ou IDs do bot.
 *
 * O portal consome somente o contrato interno abaixo. Para trocar Cakto por
 * outro checkout, altere `checkout.defaultProvider` e preencha as URLs do
 * novo provedor. A regra de acesso continua usando os mesmos product IDs
 * `site:*` e não precisa conhecer a API do checkout.
 */

// Campo canônico de atribuição do portal. Produtos do Telegram usam outro valor.
export const source_channel = "site";
export const SOURCE_CHANNEL = source_channel;

export const ACCESS_STATES = Object.freeze({
  AVAILABLE: "available",
  LOCKED: "locked",
  IN_PROGRESS: "in_progress",
  NEW: "new",
  PENDING: "pending",
  EXPIRED: "expired",
  REVOKED: "revoked",
});

export const CONTENT_TYPES = Object.freeze({
  READING: "reading",
  DAILY: "daily",
  GUIDE: "guide",
  PROFILE: "profile",
});

// IDs de produto do site. Nunca reutilizar os IDs equivalentes do Telegram.
export const PRODUCTS = Object.freeze({
  PLANO_LUA: "site:plano_lua",
  MAPA_ASTRAL: "site:mapa_astral",
  MAPA_AMOR: "site:mapa_amor_sinastria",
  MAPA_CARREIRA: "site:mapa_carreira",
  MAPA_PROSPERIDADE: "site:mapa_prosperidade",
  OFERTA_PLANO_LUA_PREMIUM: "site:oferta_plano_lua_premium",
  COMBO_ASTRAL_AMOR: "site:combo_mapa_astral_amor",
  COMBO_ASTRAL_CARREIRA: "site:combo_mapa_astral_carreira",
  COMBO_ASTRAL_PROSPERIDADE: "site:combo_mapa_astral_prosperidade",
  COMBO_AMOR_CARREIRA: "site:combo_amor_carreira",
  COMBO_AMOR_PROSPERIDADE: "site:combo_amor_prosperidade",
  COMBO_CARREIRA_PROSPERIDADE: "site:combo_carreira_prosperidade",
  COMBO_PLANO_LUA_ASTRAL: "site:combo_plano_lua_mapa_astral",
  COMBO_PLANO_LUA_AMOR: "site:combo_plano_lua_mapa_amor",
  COMBO_PLANO_LUA_PROSPERIDADE: "site:combo_plano_lua_mapa_prosperidade",
});

export const CONTENT = Object.freeze({
  WELCOME: "site:content:boas_vindas",
  HOROSCOPE_DAILY: "site:content:horoscopo_diario",
  MONTHLY_GUIDE: "site:content:guia_do_mes",
  ASTRAL_MAP: "site:content:mapa_astral_completo",
  LOVE_MAP: "site:content:mapa_do_amor_sinastria",
  CAREER_MAP: "site:content:mapa_da_carreira",
  PROSPERITY_MAP: "site:content:mapa_da_prosperidade",
  WEEKLY_FORECAST: "site:content:previsao_semanal",
  LUNAR_CALENDAR: "site:content:calendario_lunar",
  RETROGRADES_GUIDE: "site:content:guia_dos_retrogrados",
  ASCENDANT_MANUAL: "site:content:manual_do_ascendente",
});

const content = (id, title, productId, type, summary, options = {}) => ({
  id,
  source_channel,
  sourceChannel: source_channel,
  title,
  productId,
  type,
  summary,
  delivery: "web",
  defaultState: ACCESS_STATES.LOCKED,
  downloadable: true,
  download: {
    format: "pdf",
    // Endpoint preenchido pelo backend do site quando o download existir.
    url: "",
  },
  ...options,
});

/** Conteúdos são leituras web primeiro; o PDF é apenas uma saída opcional. */
export const WEB_CONTENT = Object.freeze([
  content(
    CONTENT.WELCOME,
    "Boas-vindas ao seu céu",
    null,
    CONTENT_TYPES.GUIDE,
    "Um pequeno ritual para começar a explorar sua área AstroDicas.",
    { defaultState: ACCESS_STATES.NEW, downloadable: false },
  ),
  content(
    CONTENT.HOROSCOPE_DAILY,
    "Horóscopo personalizado do dia",
    PRODUCTS.PLANO_LUA,
    CONTENT_TYPES.DAILY,
    "Trânsitos do dia interpretados a partir do seu mapa natal.",
    { recurring: true, defaultState: ACCESS_STATES.AVAILABLE },
  ),
  content(
    CONTENT.MONTHLY_GUIDE,
    "Guia do seu mês",
    PRODUCTS.PLANO_LUA,
    CONTENT_TYPES.GUIDE,
    "Os movimentos mais importantes do mês, em uma leitura contínua.",
  ),
  content(
    CONTENT.ASTRAL_MAP,
    "Mapa Astral Completo",
    PRODUCTS.MAPA_ASTRAL,
    CONTENT_TYPES.PROFILE,
    "Sol, Lua, Ascendente, casas e aspectos traduzidos para a sua vida.",
  ),
  content(
    CONTENT.LOVE_MAP,
    "Mapa do Amor / Sinastria",
    PRODUCTS.MAPA_AMOR,
    CONTENT_TYPES.READING,
    "Padrões afetivos, compatibilidade e caminhos para relações mais conscientes.",
  ),
  content(
    CONTENT.CAREER_MAP,
    "Mapa da Carreira",
    PRODUCTS.MAPA_CARREIRA,
    CONTENT_TYPES.READING,
    "Talentos, ambientes de trabalho e direção profissional do seu mapa.",
  ),
  content(
    CONTENT.PROSPERITY_MAP,
    "Mapa da Prosperidade",
    PRODUCTS.MAPA_PROSPERIDADE,
    CONTENT_TYPES.READING,
    "Sua relação com dinheiro, abundância e oportunidades de crescimento.",
  ),
  content(
    CONTENT.WEEKLY_FORECAST,
    "Previsão da semana",
    PRODUCTS.OFERTA_PLANO_LUA_PREMIUM,
    CONTENT_TYPES.GUIDE,
    "Uma visão prática dos próximos sete dias e seus melhores momentos.",
  ),
  content(
    CONTENT.LUNAR_CALENDAR,
    "Calendário Lunar",
    PRODUCTS.OFERTA_PLANO_LUA_PREMIUM,
    CONTENT_TYPES.GUIDE,
    "Fases da Lua e datas para planejar seus próximos movimentos.",
  ),
  content(
    CONTENT.RETROGRADES_GUIDE,
    "Guia dos Retrógrados",
    PRODUCTS.OFERTA_PLANO_LUA_PREMIUM,
    CONTENT_TYPES.GUIDE,
    "Como atravessar os principais ciclos retrógrados com mais clareza.",
  ),
  content(
    CONTENT.ASCENDANT_MANUAL,
    "Manual do Ascendente",
    PRODUCTS.OFERTA_PLANO_LUA_PREMIUM,
    CONTENT_TYPES.PROFILE,
    "Um guia para reconhecer a forma como você chega ao mundo.",
  ),
]);

const product = (id, slug, name, kind, description, contentIds, options = {}) => ({
  id,
  source_channel,
  sourceChannel: source_channel,
  slug,
  name,
  kind,
  description,
  status: "catalog",
  contentIds,
  ...options,
});

/** Catálogo inicial exclusivamente web, incluindo ofertas e combos. */
export const SITE_CATALOG = Object.freeze([
  product(
    PRODUCTS.PLANO_LUA,
    "plano-lua",
    "Plano Lua",
    "subscription",
    "Horóscopo personalizado, Mapa Astral e Guia do Mês na sua área.",
    [CONTENT.HOROSCOPE_DAILY, CONTENT.MONTHLY_GUIDE, CONTENT.ASTRAL_MAP],
    { billing: "recurring", featured: true },
  ),
  product(
    PRODUCTS.MAPA_ASTRAL,
    "mapa-astral",
    "Mapa Astral Completo",
    "single",
    "Uma leitura profunda do desenho único do seu céu natal.",
    [CONTENT.ASTRAL_MAP],
  ),
  product(
    PRODUCTS.MAPA_AMOR,
    "mapa-amor-sinastria",
    "Mapa do Amor / Sinastria",
    "single",
    "Compatibilidade, padrões afetivos e linguagem do encontro.",
    [CONTENT.LOVE_MAP],
  ),
  product(
    PRODUCTS.MAPA_CARREIRA,
    "mapa-carreira",
    "Mapa da Carreira",
    "single",
    "Talentos, ambientes e próximos passos profissionais.",
    [CONTENT.CAREER_MAP],
  ),
  product(
    PRODUCTS.MAPA_PROSPERIDADE,
    "mapa-prosperidade",
    "Mapa da Prosperidade",
    "single",
    "Abundância, dinheiro e oportunidades vistos pelo seu mapa.",
    [CONTENT.PROSPERITY_MAP],
  ),
  product(
    PRODUCTS.OFERTA_PLANO_LUA_PREMIUM,
    "plano-lua-premium",
    "Plano Lua Premium",
    "offer",
    "Um mês de Plano Lua com todos os bônus de leitura.",
    [
      CONTENT.HOROSCOPE_DAILY,
      CONTENT.MONTHLY_GUIDE,
      CONTENT.ASTRAL_MAP,
      CONTENT.LOVE_MAP,
      CONTENT.PROSPERITY_MAP,
      CONTENT.WEEKLY_FORECAST,
      CONTENT.LUNAR_CALENDAR,
      CONTENT.RETROGRADES_GUIDE,
      CONTENT.ASCENDANT_MANUAL,
    ],
    { billing: "one_time", featured: true },
  ),
  product(
    PRODUCTS.COMBO_ASTRAL_AMOR,
    "combo-mapa-astral-amor",
    "Mapa Astral + Mapa do Amor",
    "combo",
    "Seu mapa natal completo e uma leitura dos seus encontros.",
    [CONTENT.ASTRAL_MAP, CONTENT.LOVE_MAP],
    { includes: [PRODUCTS.MAPA_ASTRAL, PRODUCTS.MAPA_AMOR] },
  ),
  product(
    PRODUCTS.COMBO_ASTRAL_CARREIRA,
    "combo-mapa-astral-carreira",
    "Mapa Astral + Mapa da Carreira",
    "combo",
    "Identidade e direção profissional na mesma experiência.",
    [CONTENT.ASTRAL_MAP, CONTENT.CAREER_MAP],
    { includes: [PRODUCTS.MAPA_ASTRAL, PRODUCTS.MAPA_CARREIRA] },
  ),
  product(
    PRODUCTS.COMBO_ASTRAL_PROSPERIDADE,
    "combo-mapa-astral-prosperidade",
    "Mapa Astral + Mapa da Prosperidade",
    "combo",
    "Seu céu natal e os caminhos de abundância que ele revela.",
    [CONTENT.ASTRAL_MAP, CONTENT.PROSPERITY_MAP],
    { includes: [PRODUCTS.MAPA_ASTRAL, PRODUCTS.MAPA_PROSPERIDADE] },
  ),
  product(
    PRODUCTS.COMBO_AMOR_PROSPERIDADE,
    "combo-amor-prosperidade",
    "Mapa do Amor + Mapa da Prosperidade",
    "combo",
    "Relações e recursos vistos em duas leituras complementares.",
    [CONTENT.LOVE_MAP, CONTENT.PROSPERITY_MAP],
    { includes: [PRODUCTS.MAPA_AMOR, PRODUCTS.MAPA_PROSPERIDADE] },
  ),
  product(
    PRODUCTS.COMBO_AMOR_CARREIRA,
    "combo-amor-carreira",
    "Mapa do Amor + Mapa da Carreira",
    "combo",
    "Relações e direção profissional em duas leituras complementares.",
    [CONTENT.LOVE_MAP, CONTENT.CAREER_MAP],
    { includes: [PRODUCTS.MAPA_AMOR, PRODUCTS.MAPA_CARREIRA] },
  ),
  product(
    PRODUCTS.COMBO_CARREIRA_PROSPERIDADE,
    "combo-carreira-prosperidade",
    "Mapa da Carreira + Mapa da Prosperidade",
    "combo",
    "Trabalho, talento e abundância em uma jornada única.",
    [CONTENT.CAREER_MAP, CONTENT.PROSPERITY_MAP],
    { includes: [PRODUCTS.MAPA_CARREIRA, PRODUCTS.MAPA_PROSPERIDADE] },
  ),
  product(
    PRODUCTS.COMBO_PLANO_LUA_ASTRAL,
    "combo-plano-lua-mapa-astral",
    "Plano Lua + Mapa Astral",
    "combo",
    "Acompanhamento mensal e seu mapa completo em uma só compra.",
    [CONTENT.HOROSCOPE_DAILY, CONTENT.MONTHLY_GUIDE, CONTENT.ASTRAL_MAP],
    { includes: [PRODUCTS.PLANO_LUA, PRODUCTS.MAPA_ASTRAL] },
  ),
  product(
    PRODUCTS.COMBO_PLANO_LUA_AMOR,
    "combo-plano-lua-mapa-amor",
    "Plano Lua + Mapa do Amor",
    "combo",
    "Seu céu diário e uma leitura especial para os relacionamentos.",
    [CONTENT.HOROSCOPE_DAILY, CONTENT.MONTHLY_GUIDE, CONTENT.LOVE_MAP],
    { includes: [PRODUCTS.PLANO_LUA, PRODUCTS.MAPA_AMOR] },
  ),
  product(
    PRODUCTS.COMBO_PLANO_LUA_PROSPERIDADE,
    "combo-plano-lua-mapa-prosperidade",
    "Plano Lua + Mapa da Prosperidade",
    "combo",
    "Seu acompanhamento diário e uma leitura para prosperar.",
    [CONTENT.HOROSCOPE_DAILY, CONTENT.MONTHLY_GUIDE, CONTENT.PROSPERITY_MAP],
    { includes: [PRODUCTS.PLANO_LUA, PRODUCTS.MAPA_PROSPERIDADE] },
  ),
]);

const offerIds = SITE_CATALOG.map(({ id }) => id);

/**
 * Checkout é um detalhe substituível do canal site.
 * URLs vazias são intencionais: devem ser preenchidas no ambiente de deploy.
 * Para trocar a Cakto: habilite outro adaptador, preencha suas URLs e altere
 * `defaultProvider`; mantenha os mesmos IDs `site:*`. Nunca copie checkout,
 * tokens, webhooks ou URLs do Telegram para esta configuração independente.
 */
export const CHECKOUT = {
  defaultProvider: "cakto",
  providers: {
    cakto: {
      enabled: true,
      // Uma URL por oferta/produto site. Cole aqui os links reais da Cakto.
      checkoutUrls: Object.fromEntries(offerIds.map((id) => [id, ""])),
    },
    // Adaptadores futuros: habilitar e preencher sem alterar o catálogo.
    kiwify: { enabled: false, checkoutUrls: Object.fromEntries(offerIds.map((id) => [id, ""])) },
    hotmart: { enabled: false, checkoutUrls: Object.fromEntries(offerIds.map((id) => [id, ""])) },
    stripe: { enabled: false, checkoutUrls: Object.fromEntries(offerIds.map((id) => [id, ""])) },
    custom: { enabled: false, checkoutUrls: Object.fromEntries(offerIds.map((id) => [id, ""])) },
  },
};

export function checkoutUrl(productId, provider = CHECKOUT.defaultProvider) {
  const adapter = CHECKOUT.providers[provider];
  if (!adapter?.enabled) return "";
  return adapter.checkoutUrls[productId] || "";
}

export const PORTAL_CONFIG = Object.freeze({
  source_channel,
  sourceChannel: SOURCE_CHANNEL,
  accessStates: ACCESS_STATES,
  contentTypes: CONTENT_TYPES,
  productIds: PRODUCTS,
  contentIds: CONTENT,
  catalog: SITE_CATALOG,
  content: WEB_CONTENT,
  checkout: CHECKOUT,
});

export default PORTAL_CONFIG;
