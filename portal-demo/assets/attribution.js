/**
 * De onde a cliente veio — guardado no navegador dela até a compra acontecer.
 *
 * Duas origens, não uma:
 *   PRIMEIRA — quem descobriu a cliente. Gravada uma vez e nunca sobrescrita.
 *   ÚLTIMA   — quem fechou a venda. Sobrescrita a cada visita marcada.
 *
 * Por que as duas: a cliente vê o anúncio na segunda, volta pelo Google na
 * quarta e compra. Creditando só a última, o anúncio que a apresentou à marca
 * nunca aparece no relatório e a decisão de onde investir sai errada.
 *
 * Visita SEM marcação não apaga a última origem conhecida. Se apagasse, quem
 * clicou no anúncio, fechou a aba e voltou digitando o endereço viraria
 * "direto" — e o anúncio que pagou por aquela venda sumiria do painel.
 */
(function () {
  'use strict';

  var CHAVE_PRIMEIRA = 'astro_attr_first';
  var CHAVE_ULTIMA = 'astro_attr_last';
  // 90 dias: janela comum de atribuição em resposta direta, e prazo em que
  // ainda faz sentido creditar um anúncio por uma compra.
  var VALIDADE_MS = 90 * 24 * 60 * 60 * 1000;

  function guarda(chave, valor) {
    try {
      localStorage.setItem(chave, JSON.stringify(valor));
    } catch (e) {
      /* modo anônimo ou storage cheio: perder a origem nunca pode quebrar a página */
    }
  }

  function le(chave) {
    try {
      var cru = localStorage.getItem(chave);
      if (!cru) return null;
      var dado = JSON.parse(cru);
      if (!dado || !dado.ts) return null;
      if (Date.now() - dado.ts > VALIDADE_MS) return null;
      return dado;
    } catch (e) {
      return null;
    }
  }

  function normaliza(texto) {
    // Minúscula e sem espaço nas pontas: relatório agrupa por este texto, e
    // "Instagram" e "instagram" viariam duas linhas no painel.
    return String(texto || '').trim().toLowerCase().slice(0, 120);
  }

  function daUrl() {
    var params = new URLSearchParams(window.location.search);
    var origem = normaliza(params.get('utm_source') || params.get('ref'));
    if (!origem) return null;
    return {
      source: origem,
      medium: normaliza(params.get('utm_medium')),
      campaign: normaliza(params.get('utm_campaign')),
      content: normaliza(params.get('utm_content')),
      landing_page: String(window.location.pathname + window.location.search).slice(0, 300),
      referrer: String(document.referrer || '').slice(0, 300),
      ts: Date.now()
    };
  }

  var atual = daUrl();
  if (atual) {
    if (!le(CHAVE_PRIMEIRA)) guarda(CHAVE_PRIMEIRA, atual);
    guarda(CHAVE_ULTIMA, atual);
  }

  /**
   * O que mandar junto do pedido. Sempre um objeto — o backend trunca e
   * normaliza de novo, porque nada que vem do navegador é confiável.
   */
  window.astroAttribution = function () {
    var primeira = le(CHAVE_PRIMEIRA) || {};
    var ultima = le(CHAVE_ULTIMA) || {};
    return {
      first_source: primeira.source || '',
      first_medium: primeira.medium || '',
      first_campaign: primeira.campaign || '',
      first_content: primeira.content || '',
      last_source: ultima.source || '',
      last_medium: ultima.medium || '',
      last_campaign: ultima.campaign || '',
      last_content: ultima.content || '',
      // Da última visita marcada: é a que explica o clique que trouxe a compra.
      landing_page: ultima.landing_page || '',
      referrer: ultima.referrer || ''
    };
  };
})();
