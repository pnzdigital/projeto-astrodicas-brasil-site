/**
 * Widget de suporte (Chatwoot).
 *
 * Vive num arquivo próprio porque o snippet estava copiado em três páginas e
 * já tinha divergido: cada cópia decidia o idioma sozinha e todas erravam do
 * mesmo jeito. Elas liam `IS_ARGENTINA` e `document.documentElement.lang`, mas
 * os dois só existem depois que o <script type="module"> do portal roda — e
 * módulo é deferido, então este script clássico sempre chegava primeiro. Em
 * /es a bolha aparecia escrita "Suporte", em português.
 *
 * Aqui o idioma sai do caminho da URL, que já está resolvido no primeiro byte.
 *
 * Duas inboxes, uma por mercado: a conversa cai na fila do idioma certo, com
 * agentes, horário e respostas próprias. Traduzir só o rótulo da bolha não
 * resolvia — a conversa continuava chegando misturada na mesma caixa.
 */
(function () {
  var BASE = 'https://atendimento.pnzdigital.com.br';
  // websiteToken por mercado — uma inbox para cada, na conta "Astro Dicas".
  // Se algum dia o token AR ficar vazio, a Argentina cai na fila BR: mistura
  // atendimento, mas é melhor que ficar sem canal nenhum.
  var TOKENS = {
    pt: 'oH4bygfwDRAHS5kUBBMB35yz',   // inbox 23 "Suporte AstroDicas"
    es: 'SUfQd7a7cRrsc4T51AiaLYcy'    // inbox 24 "Soporte AstroDicas (AR)"
  };

  var path = window.location.pathname;
  var isAR = path === '/es' || path.indexOf('/es/') === 0 ||
    (document.documentElement.lang || '').toLowerCase().indexOf('es') === 0;

  var TOKEN = (isAR && TOKENS.es) ? TOKENS.es : TOKENS.pt;
  if (isAR && !TOKENS.es && window.console) {
    console.warn('[suporte] inbox AR não configurada — conversa vai para a fila BR');
  }

  window.chatwootSettings = {
    position: 'right',
    type: 'expanded_bubble',
    launcherTitle: isAR ? 'Soporte' : 'Suporte',
    locale: isAR ? 'es' : 'pt_BR',
    darkMode: 'auto'
  };

  var g = document.createElement('script');
  var s = document.getElementsByTagName('script')[0];
  g.src = BASE + '/packs/js/sdk.js';
  g.defer = true;
  g.async = true;
  s.parentNode.insertBefore(g, s);
  g.onload = function () {
    window.chatwootSDK.run({ websiteToken: TOKEN, baseUrl: BASE });
  };

  // Identifica a cliente logada. O portal publica window.__astroSupportUser
  // quando a sessão resolve; se o widget subir antes disso, o evento
  // chatwoot:ready cobre o outro lado da corrida.
  function astroIdentifySupport() {
    var u = window.__astroSupportUser;
    if (!u || !u.email || !window.$chatwoot) return;
    try {
      window.$chatwoot.setUser(u.email, { email: u.email, name: u.name || undefined });
    } catch (e) { /* suporte nunca pode derrubar a página */ }
  }
  window.addEventListener('chatwoot:ready', astroIdentifySupport);
  window.astroIdentifySupport = astroIdentifySupport;
})();
