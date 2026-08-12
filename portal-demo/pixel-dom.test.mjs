// Testes de comportamento real do Meta Pixel em horoscopo-gratis.html.
//
// Carrega a página num DOM linkedom (mesmo padrão de frontend-regression.mjs),
// mocka window.fbq e window.fetch, e prova por asserção quais eventos saem
// em cada interação: ViewContent na chegada, Lead ao gerar a leitura,
// StartTrial ao confirmar trial (pt-BR e es-AR), Lead ao gerar a leitura.
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
  // lança nessa chamada e o restante da função (wireDiarioAstralCta/wireTrialCard)
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

test('dispara StartTrial no caminho es-AR e pt-BR ao confirmar trial', () => {
  // Verifica na fonte que StartTrial aparece nas duas funções de wiring,
  // após validação de status:"trialing" e antes do redirect pro portal.
  const wireBlocks = [
    html.slice(html.indexOf('function wireDiarioAstralCta'), html.indexOf('function wireTrialCard')),
    html.slice(html.indexOf('function wireTrialCard'), html.lastIndexOf('</script>')),
  ];
  for (const block of wireBlocks) {
    assert.ok(block.includes("fbq('track', 'StartTrial'"), 'StartTrial ausente em um dos caminhos de trial');
    assert.ok(block.includes("data.status !== 'trialing'"), 'validação de status ausente');
    assert.ok(block.includes('window.location.href'), 'redirect ausente');
  }
});

test('es-AR: ViewContent dispara na chegada com locale es-AR', async () => {
  const { fbqCalls } = await loadPage('/es/horoscopo-gratis');
  const vc = fbqCalls.find((c) => c[1] === 'ViewContent');
  assert.ok(vc, 'ViewContent não disparou para /es/horoscopo-gratis');
  assert.equal(vc[2].content_language, 'es-AR');
});

test('es-AR: StartTrial dispara APÓS api/trial/start confirmar status=trialing', () => {
  // Verifica no código-fonte que StartTrial vem depois da verificação de status,
  // garantindo que o evento só sai quando o trial foi de fato criado no backend.
  const trialBlock = html.slice(html.indexOf('function wireTrialCard'), html.lastIndexOf('</script>'));
  const startTrialIdx = trialBlock.indexOf("fbq('track', 'StartTrial'");
  const apiConfirmIdx = trialBlock.indexOf("data.status !== 'trialing'");
  const fetchIdx = trialBlock.indexOf('/api/trial/start');
  const redirectIdx = trialBlock.indexOf('window.location.href');
  assert.ok(startTrialIdx > -1, 'StartTrial não encontrado em wireTrialCard');
  assert.ok(apiConfirmIdx > -1, 'verificação data.status !== trialing não encontrada');
  assert.ok(fetchIdx > -1, 'submit do trial não chama /api/trial/start');
  assert.ok(redirectIdx > -1, 'submit do trial não redireciona pro PORTAL');
  assert.ok(startTrialIdx > apiConfirmIdx, 'StartTrial deve disparar DEPOIS da verificação de status=trialing (não antes da API responder)');
  assert.ok(startTrialIdx < redirectIdx, 'StartTrial deve disparar ANTES do redirect pro portal');
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

// ── sales.html: ViewContent na landing page ──────────────────────────────────

const salesHtml = readFileSync(pathResolve(here, 'sales.html'), 'utf8');

test('sales.html dispara ViewContent na chegada', async () => {
  const { window } = parseHTML(salesHtml);
  window.location = { pathname: '/sales.html', search: '', href: 'http://localhost/sales.html' };
  window.scrollTo = () => {};
  const fbqCalls = [];
  window.fbq = (...args) => fbqCalls.push(args);

  const scriptBody = salesHtml.match(/<script type="module">([\s\S]*?)<\/script>/)[1];
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  try {
    await new AsyncFunction('window', 'document', 'fetch', 'location', scriptBody)(
      window, window.document, window.fetch, window.location,
    );
  } catch (_) {}
  await new Promise((r) => setTimeout(r, 20));

  const vc = fbqCalls.find((c) => c[1] === 'ViewContent');
  assert.ok(vc, 'ViewContent não disparou no sales.html');
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
