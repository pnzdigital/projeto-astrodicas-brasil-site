/**
 * Configuração Argentina do portal web AstroDicas.
 *
 * Mesmo canal site e mesmos IDs site:*; apenas idioma, moeda e checkout mudam.
 * O Telegram continua fora deste arquivo e deste fluxo.
 */
import base from "./portal-config.js";

const ARS_PRICES = Object.freeze({
  "site:plano_lua": "ARS 9.900 / mes",
  "site:mapa_astral": "Desde ARS 16.900",
  "site:mapa_amor_sinastria": "Desde ARS 16.900",
  "site:mapa_carreira": "Desde ARS 16.900",
  "site:mapa_prosperidade": "Desde ARS 16.900",
  "site:oferta_plano_lua_premium": "ARS 34.900",
  "site:combo_mapa_astral_amor": "ARS 27.900",
  "site:combo_mapa_astral_carreira": "ARS 27.900",
  "site:combo_mapa_astral_prosperidade": "ARS 27.900",
  "site:combo_amor_carreira": "ARS 27.900",
  "site:combo_amor_prosperidade": "ARS 27.900",
  "site:combo_carreira_prosperidade": "ARS 27.900",
  "site:combo_plano_lua_mapa_astral": "ARS 23.900",
  "site:combo_plano_lua_mapa_amor": "ARS 23.900",
  "site:combo_plano_lua_mapa_prosperidade": "ARS 23.900",
});

const productCatalog = base.catalog.map((product) => ({
  ...product,
  locale: "es-AR",
  currency: "ARS",
  localizedPrice: ARS_PRICES[product.id] || "Consultar precio",
}));

const productIds = productCatalog.map(({ id }) => id);
const checkoutUrls = Object.fromEntries(productIds.map((id) => [id, ""]));

export const CHECKOUT = Object.freeze({
  defaultProvider: "mercado_pago",
  providers: {
    mercado_pago: {
      enabled: true,
      mode: "transparent",
      checkoutUrls,
    },
    cakto: { enabled: false, checkoutUrls: Object.fromEntries(productIds.map((id) => [id, ""])) },
    kiwify: { enabled: false, checkoutUrls: Object.fromEntries(productIds.map((id) => [id, ""])) },
    hotmart: { enabled: false, checkoutUrls: Object.fromEntries(productIds.map((id) => [id, ""])) },
    stripe: { enabled: false, checkoutUrls: Object.fromEntries(productIds.map((id) => [id, ""])) },
    custom: { enabled: false, checkoutUrls: Object.fromEntries(productIds.map((id) => [id, ""])) },
  },
});

const PORTAL_CONFIG = Object.freeze({
  ...base,
  locale: "es-AR",
  country: "AR",
  currency: "ARS",
  catalog: productCatalog,
  checkout: CHECKOUT,
});

export default PORTAL_CONFIG;
