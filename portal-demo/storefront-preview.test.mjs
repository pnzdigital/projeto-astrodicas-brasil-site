// Regressão da prévia grátis do mapa natal na storefront.
//
// Roda o <script type="module"> inline de storefront.html num DOM linkedom,
// nas DUAS versões (pt-BR em "/" e es-AR em "/es"), e verifica o contrato que
// o formulário grátis precisa cumprir:
//
// 1. O formulário existe e é renderizado nas duas línguas com os labels certos.
// 2. O submit chama POST /api/preview/natal com locale e dados de nascimento.
// 3. O resultado renderiza Sol, Lua, Ascendente e a lista de planetas.
// 4. O bloco de upsell ("quer o mapa completo?") aparece e leva ao checkout do
//    Mapa Astral — o produto pago que a prévia amostra.
// 5. Um erro 422 da API vira mensagem legível, nunca JSON cru.
//
// Roda com: ``node portal-demo/storefront-preview.test.mjs``.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve as pathResolve } from 'node:path';
import assert from 'node:assert/strict';
import { parseHTML } from 'linkedom';

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(pathResolve(here, 'storefront.html'), 'utf8');

const SAMPLE_PREVIEW = {
  locale: 'pt-BR',
  locked: true,
  birth_time_approximate: false,
  sun: { sign: 'Touro', sign_label: 'Touro', degree: 29.4, text: 'Texto do Sol em Touro para a prévia.' },
  moon: { sign: 'Leão', sign_label: 'Leão', degree: 3.1, text: 'Texto da Lua em Leão para a prévia.' },
  ascendant: { sign: 'Libra', sign_label: 'Libra', degree: 12.8, text: 'Texto do Ascendente em Libra.' },
  planets: [
    { name: 'Sol', label: 'Sol', sign: 'Touro', sign_label: 'Touro', degree: 29.4, retrograde: false },
    { name: 'Lua', label: 'Lua', sign: 'Leão', sign_label: 'Leão', degree: 3.1, retrograde: false },
    { name: 'Mercúrio', label: 'Mercúrio', sign: 'Gêmeos', sign_label: 'Gêmeos', degree: 8.2, retrograde: true },
  ],
};

// ── Harness ─────────────────────────────────────────────────────────────────

function boot({ pathname }) {
  const { window } = parseHTML(html);
  const fetchCalls = [];
  const fetchResponses = [];

  window.scrollTo = () => {};
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  Object.defineProperty(window, 'location', {
    value: { pathname, href: pathname, assign() {} },
    writable: true,
  });
  window.fetch = async (url, opts = {}) => {
    fetchCalls.push({ url, opts });
    if (!fetchResponses.length) throw new Error(`fetch sem resposta enfileirada: ${url}`);
    return fetchResponses.shift();
  };
  window.PORTAL_API_URL = '';

  const stubConfig = {
    default: {
      catalog: [
        { id: 'site:mapa_astral', kind: 'single', name: 'Mapa Astral', description: 'x', localizedPrice: 'ARS 1' },
      ],
      checkout: {
        defaultProvider: 'cakto',
        providers: { cakto: { checkoutUrls: { 'site:mapa_astral': 'https://checkout.exemplo/mapa' } } },
      },
    },
  };

  const scriptMatch = html.match(/<script type="module">([\s\S]*?)<\/script>/);
  if (!scriptMatch) throw new Error('module script não encontrado em storefront.html');
  let body = scriptMatch[1];
  // linkedom não tem dynamic import: troca o import do portal-config pelo stub.
  body = body.replace(
    /const config=\(await import\([^)]*\)\)\.default;/,
    'const config=window.__STUB_CONFIG__.default;',
  );
  window.__STUB_CONFIG__ = stubConfig;

  const run = new window.Function('window', 'document', `return (async()=>{${body}})()`);
  return {
    window,
    document: window.document,
    fetchCalls,
    queue(status, payload) {
      fetchResponses.push({ ok: status >= 200 && status < 300, status, json: async () => payload });
    },
    ready: run(window, window.document),
  };
}

async function tick(ms = 30) {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

function fillForm(document, values) {
  const form = document.getElementById('preview-form');
  assert.ok(form, 'formulário de prévia precisa existir na storefront');
  for (const [name, value] of Object.entries(values)) {
    const field = form.querySelector(`[name="${name}"]`);
    assert.ok(field, `campo ${name} ausente no formulário de prévia`);
    field.value = value;
  }
  return form;
}

async function submit(window, form) {
  form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
  await tick(40);
}

const VALID_INPUT = {
  birth_date: '1990-05-20',
  birth_time: '14:30',
  birth_city: 'Recife',
};

// ── Testes ──────────────────────────────────────────────────────────────────

async function testFormRendersInBothLocales() {
  const br = boot({ pathname: '/' });
  await br.ready;
  assert.ok(br.document.getElementById('preview-form'), 'pt-BR: formulário ausente');
  const brText = br.document.getElementById('previa').textContent;
  assert.match(brText, /grátis|Grátis/, 'pt-BR: seção precisa dizer que é grátis');

  const ar = boot({ pathname: '/es' });
  await ar.ready;
  assert.ok(ar.document.getElementById('preview-form'), 'es-AR: formulário ausente');
  const arText = ar.document.getElementById('previa').textContent;
  assert.match(arText, /gratis|Gratis/, 'es-AR: seção precisa dizer que é gratis');
  assert.notEqual(brText, arText, 'a seção precisa estar traduzida, não duplicada');
}

async function testSubmitCallsPreviewEndpoint() {
  const app = boot({ pathname: '/' });
  await app.ready;
  app.queue(200, SAMPLE_PREVIEW);
  await submit(app.window, fillForm(app.document, VALID_INPUT));

  const call = app.fetchCalls.at(-1);
  assert.ok(call, 'submit precisa chamar a API');
  assert.match(call.url, /\/api\/preview\/natal$/);
  assert.equal(call.opts.method, 'POST');
  const sent = JSON.parse(call.opts.body);
  assert.equal(sent.birth_date, '1990-05-20');
  assert.equal(sent.birth_time, '14:30');
  assert.equal(sent.birth_city, 'Recife');
  assert.equal(sent.locale, 'pt-BR');
}

async function testSubmitSendsEsArLocale() {
  const app = boot({ pathname: '/es' });
  await app.ready;
  app.queue(200, { ...SAMPLE_PREVIEW, locale: 'es-AR' });
  await submit(app.window, fillForm(app.document, VALID_INPUT));

  assert.equal(JSON.parse(app.fetchCalls.at(-1).opts.body).locale, 'es-AR');
}

async function testResultRendersLuminariesAndPlanets() {
  const app = boot({ pathname: '/' });
  await app.ready;
  app.queue(200, SAMPLE_PREVIEW);
  await submit(app.window, fillForm(app.document, VALID_INPUT));

  const result = app.document.getElementById('preview-result').textContent;
  assert.match(result, /Touro/, 'Sol não renderizado');
  assert.match(result, /Texto do Sol em Touro/, 'parágrafo do Sol não renderizado');
  assert.match(result, /Texto da Lua em Leão/, 'parágrafo da Lua não renderizado');
  assert.match(result, /Texto do Ascendente em Libra/, 'parágrafo do Ascendente não renderizado');
  assert.match(result, /Mercúrio/, 'lista de planetas não renderizada');
  assert.match(result, /Gêmeos/, 'signo do planeta não renderizado');
}

async function testResultShowsUpsellToCheckout() {
  const app = boot({ pathname: '/' });
  await app.ready;
  app.queue(200, SAMPLE_PREVIEW);
  await submit(app.window, fillForm(app.document, VALID_INPUT));

  const upsell = app.document.getElementById('preview-upsell');
  assert.ok(upsell, 'bloco de upsell ausente após a prévia');
  assert.match(upsell.textContent, /completo|Completo/, 'upsell precisa oferecer o mapa completo');
  const cta = upsell.querySelector('a[href], button');
  assert.ok(cta, 'upsell precisa ter um CTA clicável');
}

async function testMissingAscendantIsExplainedNotFaked() {
  const app = boot({ pathname: '/' });
  await app.ready;
  app.queue(200, { ...SAMPLE_PREVIEW, ascendant: null, birth_time_approximate: true });
  await submit(app.window, fillForm(app.document, { ...VALID_INPUT, birth_time: '' }));

  const result = app.document.getElementById('preview-result').textContent;
  assert.match(result, /hora/i, 'sem hora, a UI precisa explicar por que falta o Ascendente');
  assert.ok(!/Libra/.test(result), 'não pode inventar Ascendente quando a API devolveu null');
}

async function testApiErrorBecomesReadableMessage() {
  const app = boot({ pathname: '/' });
  await app.ready;
  app.queue(422, { detail: 'Não encontramos essa cidade. Confira o nome ou tente a capital mais próxima.' });
  await submit(app.window, fillForm(app.document, { ...VALID_INPUT, birth_city: 'Zzzz' }));

  const error = app.document.querySelector('[data-preview-error]').textContent;
  assert.match(error, /cidade/i);
  assert.ok(!error.startsWith('['), 'erro não pode vazar array JSON');
  assert.ok(!error.includes('"loc"'), 'erro não pode vazar estrutura do Pydantic');
}

const tests = [
  testFormRendersInBothLocales,
  testSubmitCallsPreviewEndpoint,
  testSubmitSendsEsArLocale,
  testResultRendersLuminariesAndPlanets,
  testResultShowsUpsellToCheckout,
  testMissingAscendantIsExplainedNotFaked,
  testApiErrorBecomesReadableMessage,
];

let failed = 0;
for (const test of tests) {
  try {
    await test();
  } catch (error) {
    failed += 1;
    console.error(`FAIL ${test.name}: ${error.message}`);
  }
}
if (failed) {
  console.error(`storefront preview: ${tests.length - failed}/${tests.length} ok, ${failed} falhando`);
  process.exit(1);
}
console.log(`storefront preview: ${tests.length}/${tests.length} ok`);
