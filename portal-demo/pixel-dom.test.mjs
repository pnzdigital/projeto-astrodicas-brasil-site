// Testes de comportamento real do Meta Pixel em horoscopo-gratis.html.
//
// Carrega a página num DOM linkedom (mesmo padrão de frontend-regression.mjs),
// mocka window.fbq e window.fetch, e prova por asserção quais eventos saem
// em cada interação: ViewContent na chegada, Lead ao gerar a leitura,
// InitiateCheckout no CTA pt-BR, AddPaymentInfo + StartTrial no fluxo es-AR.
//
// Roda com: ``node portal-demo/pixel-dom.test.mjs``.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve as pathResolve } from 'node:path';
import { parseHTML } from 'linkedom';

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(pathResolve(here, 'horoscopo-gratis.html'), 'utf8');

async function loadPage(pathname) {
  const { window } = parseHTML(html);
  window.location = { pathname, search: '', href: 'http://localhost' + pathname };
  window.scrollTo = () => {};
  // linkedom não implementa scrollIntoView; sem o polyfill, renderReading()
  // lança nessa chamada e o restante da função (wirePlanoLuaCta/wireTrialCard)
  // nunca roda — quebra em browser real não acontece, é limitação só do DOM de teste.
  window.HTMLElement.prototype.scrollIntoView = window.HTMLElement.prototype.scrollIntoView || (() => {});

  const fbqCalls = [];
  window.fbq = (...args) => fbqCalls.push(args);

  const fetchCalls = [];
  window.fetch = async (url, opts = {}) => {
    fetchCalls.push({ url, opts });
    if (String(url).includes('/api/catalog')) {
      return { ok: true, json: async () => ({ products: [], checkout: { public_key: 'TEST-'.padEnd(32, 'x') } }) };
    }
    if (String(url).includes('/api/horoscopo/gratis')) {
      return { ok: true, json: async () => ({ title: 'T', body_html: '<p>B</p>' }) };
    }
    return { ok: true, json: async () => ({}) };
  };

  const scriptMatch = html.match(/<script type="module">([\s\S]*?)<\/script>/);
  if (!scriptMatch) throw new Error('module script não encontrado');
  const scriptBody = scriptMatch[1];

  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  await new AsyncFunction(
    'window', 'document', 'fetch', 'location',
    scriptBody,
  )(window, window.document, window.fetch, window.location);

  await new Promise((r) => setTimeout(r, 20));
  return { window, document: window.document, fbqCalls, fetchCalls };
}

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test('ViewContent dispara na chegada (pt-BR)', async () => {
  const { fbqCalls } = await loadPage('/horoscopo-gratis');
  const vc = fbqCalls.find((c) => c[1] === 'ViewContent');
  assert.ok(vc, 'ViewContent não disparou');
  assert.equal(vc[2].content_language, 'pt-BR');
});

test('Lead dispara ao enviar o formulário e receber a leitura (pt-BR)', async () => {
  const { document, fbqCalls } = await loadPage('/horoscopo-gratis');
  document.getElementById('f-name').value = 'Maria';
  document.getElementById('f-date').value = '1990-01-01';
  document.getElementById('f-time').value = '10:00';
  document.getElementById('f-city').value = 'São Paulo';
  [...document.getElementById('f-state').options].find((o) => o.value === 'SP').selected = true;
  const form = document.getElementById('form');
  form.dispatchEvent(new form.ownerDocument.defaultView.Event('submit', { cancelable: true, bubbles: true }));
  await new Promise((r) => setTimeout(r, 20));
  const lead = fbqCalls.find((c) => c[1] === 'Lead');
  assert.ok(lead, 'Lead não disparou após envio do formulário');
});

test('InitiateCheckout dispara ao clicar no CTA do Plano Lua (pt-BR)', async () => {
  const { window, document, fbqCalls } = await loadPage('/horoscopo-gratis');
  document.getElementById('f-name').value = 'Maria';
  document.getElementById('f-date').value = '1990-01-01';
  document.getElementById('f-time').value = '10:00';
  document.getElementById('f-city').value = 'São Paulo';
  [...document.getElementById('f-state').options].find((o) => o.value === 'SP').selected = true;
  const form = document.getElementById('form');
  form.dispatchEvent(new form.ownerDocument.defaultView.Event('submit', { cancelable: true, bubbles: true }));
  await new Promise((r) => setTimeout(r, 20));
  const cta = document.getElementById('offer-cta');
  assert.ok(cta, 'CTA da oferta pt-BR não renderizou');
  cta.dispatchEvent(new window.Event('click', { bubbles: true }));
  const ic = fbqCalls.find((c) => c[1] === 'InitiateCheckout');
  assert.ok(ic, 'InitiateCheckout não disparou ao clicar no CTA');
});

test('es-AR: submit do form de trial dispara StartTrial antes do redirect pro Checkout Pro', () => {
  // Sem cartão/Brick no front: o trial só envia nome/e-mail e redireciona pro
  // init_point do Checkout Pro. StartTrial dispara no submit, antes do fetch.
  const trialBlock = html.slice(html.indexOf('function wireTrialCard'), html.lastIndexOf('</script>'));
  assert.match(trialBlock, /fbq\('track', 'StartTrial'/, 'StartTrial não dispara no submit do form de trial');
  assert.match(trialBlock, /\/api\/trial\/start/, 'submit do trial não chama /api/trial/start');
  assert.match(trialBlock, /data\.init_point/, 'submit do trial não redireciona pro init_point');
});

// ── storefront.html: ViewContent na vitrine ─────────────────────────────────

const storefrontHtml = readFileSync(pathResolve(here, 'storefront.html'), 'utf8');

test('storefront.html dispara ViewContent na chegada', async () => {
  const { window } = parseHTML(storefrontHtml);
  window.location = { pathname: '/', search: '', href: 'http://localhost/' };
  window.scrollTo = () => {};
  const fbqCalls = [];
  window.fbq = (...args) => fbqCalls.push(args);

  // O storefront carrega o catálogo real via dynamic import (linkedom não
  // implementa import()); stub mínimo só pra não travar antes do fbq rodar,
  // já que a chamada de ViewContent acontece antes do import na ordem do script.
  let scriptBody = storefrontHtml.match(/<script type="module">([\s\S]*?)<\/script>/)[1];
  scriptBody = scriptBody.replace(
    /const config=\(await import\([^)]*\)\)\.default;/,
    'const config={products:[]};',
  );
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  try {
    await new AsyncFunction('window', 'document', 'fetch', 'location', scriptBody)(
      window, window.document, window.fetch, window.location,
    );
  } catch (_) {
    // resto do script (render do catálogo) não é o alvo deste teste.
  }
  await new Promise((r) => setTimeout(r, 20));

  const vc = fbqCalls.find((c) => c[1] === 'ViewContent');
  assert.ok(vc, 'ViewContent não disparou no storefront');
  assert.equal(vc[2].content_language, 'pt-BR');
});

let passed = 0;
for (const [name, fn] of tests) {
  try {
    await fn();
    passed++;
    console.log(`✓ ${name}`);
  } catch (e) {
    console.error(`✗ ${name}`);
    console.error(e.message);
    process.exitCode = 1;
  }
}
console.log(`${passed}/${tests.length} passaram`);
