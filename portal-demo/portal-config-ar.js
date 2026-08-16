/**
 * Configuração Argentina do portal web AstroDicas.
 *
 * Mesmo canal site e mesmos IDs site:*; apenas idioma, moeda e checkout mudam.
 * O Telegram continua fora deste arquivo e deste fluxo.
 */
import base from "./portal-config.js";

const ARS_PRICES = Object.freeze({
  "site:diario_astral": "ARS 14.900",
  "site:mapa_astral": "ARS 12.819",
  "site:mapa_amor_sinastria": "ARS 12.819",
  "site:mapa_carreira": "ARS 12.819",
  "site:mapa_prosperidade": "ARS 12.819",
  "site:diario_astral_completo": "ARS 34.900",
  "site:combo_mapa_astral_amor": "ARS 20.259",
  "site:combo_mapa_astral_carreira": "ARS 20.259",
  "site:combo_mapa_astral_prosperidade": "ARS 20.259",
  "site:combo_amor_carreira": "ARS 20.259",
  "site:combo_amor_prosperidade": "ARS 20.259",
  "site:combo_carreira_prosperidade": "ARS 20.259",
  "site:combo_diario_astral_mapa_astral": "ARS 17.469",
  "site:combo_diario_astral_mapa_amor": "ARS 17.469",
  "site:combo_diario_astral_mapa_prosperidade": "ARS 17.469",
});

// Precio anclado (tachado): sólo exhibición, espejo de pricing.ANCHOR_ARS_MINOR
// (mapas y combos ya no salen de la conversión BRL_TO_ARS: tienen precio propio
// en ARS desde el reajuste del 16/08/2026). Nunca se cobra — el monto sale del
// backend.
const ARS_ANCHORS = Object.freeze({
  "site:mapa_astral": "ARS 16.570",
  "site:mapa_amor_sinastria": "ARS 16.570",
  "site:mapa_carreira": "ARS 16.570",
  "site:mapa_prosperidade": "ARS 16.570",
  "site:combo_mapa_astral_amor": "ARS 26.490",
  "site:combo_mapa_astral_carreira": "ARS 26.490",
  "site:combo_mapa_astral_prosperidade": "ARS 26.490",
  "site:combo_amor_carreira": "ARS 26.490",
  "site:combo_amor_prosperidade": "ARS 26.490",
  "site:combo_carreira_prosperidade": "ARS 26.490",
  "site:combo_diario_astral_mapa_astral": "ARS 22.770",
  "site:combo_diario_astral_mapa_amor": "ARS 22.770",
  "site:combo_diario_astral_mapa_prosperidade": "ARS 22.770",
});

const PRODUCT_COPY = Object.freeze({
  "site:diario_astral": ["Diario Astral", "Horóscopo personalizado, Carta Astral y Guía del Mes en tu área."],
  "site:mapa_astral": ["Carta Astral Completa", "Una lectura profunda del dibujo único de tu cielo natal."],
  "site:mapa_amor_sinastria": ["Mapa del Amor / Sinastría", "Compatibilidad, patrones afectivos y lenguaje del encuentro."],
  "site:mapa_carreira": ["Mapa de la Carrera", "Talentos, ambientes y próximos pasos profesionales."],
  "site:mapa_prosperidade": ["Mapa de la Prosperidad", "Tu relación con el dinero, la abundancia y las oportunidades de crecimiento."],
  "site:diario_astral_completo": ["Diario Astral Completo", "Un mes de Diario Astral más Amor, Prosperidad, calendario, retrógrados y Ascendente."],
  "site:combo_mapa_astral_amor": ["Carta Astral + Mapa del Amor", "Tu carta natal completa y una lectura de tus encuentros."],
  "site:combo_mapa_astral_carreira": ["Carta Astral + Mapa de la Carrera", "Identidad y dirección profesional en una misma experiencia."],
  "site:combo_mapa_astral_prosperidade": ["Carta Astral + Mapa de la Prosperidad", "Tu cielo natal y los caminos de abundancia que revela."],
  "site:combo_amor_carreira": ["Mapa del Amor + Mapa de la Carrera", "Relaciones y dirección profesional en dos lecturas complementarias."],
  "site:combo_amor_prosperidade": ["Mapa del Amor + Mapa de la Prosperidad", "Vínculos y recursos vistos desde dos lecturas complementarias."],
  "site:combo_carreira_prosperidade": ["Mapa de la Carrera + Mapa de la Prosperidad", "Trabajo, talento y abundancia en un mismo recorrido."],
  "site:combo_diario_astral_mapa_astral": ["Diario Astral + Carta Astral", "Acompañamiento diario y tu carta natal completa."],
  "site:combo_diario_astral_mapa_amor": ["Diario Astral + Mapa del Amor", "Tu cielo diario y una lectura especial para tus vínculos."],
  "site:combo_diario_astral_mapa_prosperidade": ["Diario Astral + Mapa de la Prosperidad", "Acompañamiento diario y una lectura sobre tus recursos."],
});

const CONTENT_COPY = Object.freeze({
  "site:content:boas_vindas": ["Bienvenida a tu cielo", "Un pequeño ritual para empezar a explorar tu área AstroDicas."],
  "site:content:horoscopo_diario": ["Horóscopo personalizado del día", "Los tránsitos del día interpretados desde tu mapa natal."],
  "site:content:guia_do_mes": ["Guía de tu mes", "Los movimientos importantes del mes en una lectura continua."],
  "site:content:mapa_astral_completo": ["Carta Astral Completa", "Sol, Luna, Ascendente, casas y aspectos traducidos a tu vida."],
  "site:content:mapa_do_amor_sinastria": ["Mapa del Amor / Sinastría", "Patrones afectivos, compatibilidad y caminos para vínculos conscientes."],
  "site:content:mapa_da_carreira": ["Mapa de la Carrera", "Talentos, ambientes de trabajo y dirección profesional de tu mapa."],
  "site:content:mapa_da_prosperidade": ["Mapa de la Prosperidad", "Tu relación con los recursos, la abundancia y el crecimiento."],
  "site:content:previsao_semanal": ["Previsión de la semana", "Una visión práctica de los próximos siete días y sus mejores momentos."],
  "site:content:calendario_lunar": ["Calendario Lunar", "Fases de la Luna y fechas para planear tus próximos movimientos."],
  "site:content:guia_dos_retrogrados": ["Guía de los Retrógrados", "Cómo atravesar los ciclos retrógrados con más claridad."],
  "site:content:manual_do_ascendente": ["Manual del Ascendente", "Una guía para reconocer la forma en que llegás al mundo."],
});

const productCatalog = base.catalog.map((product) => {
  const [name, description] = PRODUCT_COPY[product.id] || [product.name, product.description];
  return { ...product, name, description, locale: "es-AR", currency: "ARS", localizedPrice: ARS_PRICES[product.id] || "Consultar precio", localizedAnchor: ARS_ANCHORS[product.id] || null };
});

const webContent = base.content.map((entry) => {
  const [title, summary] = CONTENT_COPY[entry.id] || [entry.title, entry.summary];
  return { ...entry, title, summary, locale: "es-AR" };
});

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
  content: webContent,
  checkout: CHECKOUT,
});

export default PORTAL_CONFIG;
