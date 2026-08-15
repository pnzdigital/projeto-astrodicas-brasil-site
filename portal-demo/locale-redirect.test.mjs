import { test } from 'node:test';
import { strict as assert } from 'node:assert';

// Reproduz a lógica de maybeRedirectForLocale (nível 1 — user.locale da conta).
function buildRedirectTarget(userLocale, currentPath, currentSearch = '', currentHash = '') {
  if (!userLocale) return null;
  const IS_ARGENTINA = currentPath === '/es' || currentPath.startsWith('/es/');
  const userEs = userLocale.startsWith('es');
  if (userEs && !IS_ARGENTINA) return '/es' + currentSearch + currentHash;
  if (!userEs && IS_ARGENTINA) return '/' + currentSearch + currentHash;
  return null;
}

// Reproduz a lógica de nível 3 (navigator.language) do bootstrap.
// Ganha apenas quando: deslogado + path raiz + sem _loc_override.
// Prioridade: userLocale (nível 1) > path /es (nível 2) > navigator (nível 3).
function buildNavRedirect({ navLang, currentPath, authenticated, locOverride = false }) {
  const IS_ARGENTINA = currentPath === '/es' || currentPath.startsWith('/es/');
  if (authenticated) return null;       // nível 1 toma conta
  if (IS_ARGENTINA) return null;        // nível 2 respeita o path
  if (locOverride) return null;         // flag anti-loop ativa
  const lang = navLang || '';
  if (lang.startsWith('es')) return '/es';
  return null;
}

// ------------------------------------------------------------------
// Nível 1: user.locale
// ------------------------------------------------------------------
test('es-AR em / → redireciona para /es', () => {
  assert.equal(buildRedirectTarget('es-AR', '/'), '/es');
});

test('es-AR em /es → sem redirect (sem loop)', () => {
  assert.equal(buildRedirectTarget('es-AR', '/es'), null);
});

test('es-AR em /es/ → sem redirect', () => {
  assert.equal(buildRedirectTarget('es-AR', '/es/'), null);
});

test('pt-BR em / → sem redirect', () => {
  assert.equal(buildRedirectTarget('pt-BR', '/'), null);
});

test('pt-BR em /es → redireciona para /', () => {
  assert.equal(buildRedirectTarget('pt-BR', '/es'), '/');
});

test('es-AR em / preserva query string', () => {
  assert.equal(buildRedirectTarget('es-AR', '/', '?demo=paid'), '/es?demo=paid');
});

test('es-AR em / preserva hash de leitura', () => {
  assert.equal(buildRedirectTarget('es-AR', '/', '', '#/leitura/site:content:horoscopo_diario'), '/es#/leitura/site:content:horoscopo_diario');
});

test('es-AR em / preserva query e hash juntos', () => {
  assert.equal(buildRedirectTarget('es-AR', '/', '?demo=paid', '#entregaveis'), '/es?demo=paid#entregaveis');
});

test('sem locale → sem redirect', () => {
  assert.equal(buildRedirectTarget(null, '/'), null);
  assert.equal(buildRedirectTarget(undefined, '/'), null);
  assert.equal(buildRedirectTarget('', '/'), null);
});

test('es-MX (outro dialeto es) em / → redireciona para /es', () => {
  assert.equal(buildRedirectTarget('es-MX', '/'), '/es');
});

// ------------------------------------------------------------------
// Nível 3: navigator.language (apenas deslogado, raiz, sem override)
// ------------------------------------------------------------------

test('nível 3: visitante deslogado com browser es em / → /es', () => {
  assert.equal(buildNavRedirect({ navLang: 'es-AR', currentPath: '/', authenticated: false }), '/es');
});

test('nível 3: browser es-MX deslogado em / → /es', () => {
  assert.equal(buildNavRedirect({ navLang: 'es-MX', currentPath: '/', authenticated: false }), '/es');
});

test('nível 3: browser pt-BR deslogado em / → sem redirect', () => {
  assert.equal(buildNavRedirect({ navLang: 'pt-BR', currentPath: '/', authenticated: false }), null);
});

test('nível 3: browser es mas já em /es (nível 2 ganha) → sem redirect', () => {
  assert.equal(buildNavRedirect({ navLang: 'es-AR', currentPath: '/es', authenticated: false }), null);
});

test('nível 3: browser es mas logado (nível 1 ganha) → sem redirect', () => {
  // Conta pt-BR logada com browser es: nível 1 toma conta, nível 3 não dispara.
  assert.equal(buildNavRedirect({ navLang: 'es-AR', currentPath: '/', authenticated: true }), null);
});

test('nível 3: conta pt-BR em /es ativa _loc_override → sem re-redirect', () => {
  // maybeRedirectForLocale seta _loc_override e vai para '/'.
  // No próximo load em '/', o override impede que o browser es reenvie para /es.
  assert.equal(buildNavRedirect({ navLang: 'es-AR', currentPath: '/', authenticated: false, locOverride: true }), null);
});
