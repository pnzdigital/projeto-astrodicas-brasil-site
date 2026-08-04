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

async function testMissingBirthTimeShowsAscendantWithWarning() {
  const app = boot({ pathname: '/' });
  await app.ready;
  app.queue(200, {
    ...SAMPLE_PREVIEW,
    ascendant: { sign: 'Áries', sign_label: 'Áries', degree: 4.2, text: 'Ascendente em Áries estimado.' },
    birth_time_approximate: true,
    birth_time_assumed: true,
    ascendant_warning: {
      'pt-BR':
        'Hora de nascimento não informada — assumimos 00:00 só para mostrar um valor. O Ascendente é o ponto mais sensível à hora do mapa inteiro (troca de signo a cada ~2h), então este resultado é uma ESTIMATIVA.',
      'es-AR':
        'Hora de nacimiento no informada — asumimos 00:00 solo para mostrar un valor. El Ascendente es el punto más sensible a la hora de toda la carta (cambia de signo cada ~2h), así que este resultado es una ESTIMACIÓN.',
    },
  });
  await submit(app.window, fillForm(app.document, { ...VALID_INPUT, birth_time: '' }));

  const result = app.document.getElementById('preview-result').textContent;
  assert.match(result, /Áries/, 'Ascendente assumido precisa aparecer no resultado');
  assert.match(result, /ESTIMATIVA|estimativa/i, 'aviso PT precisa dizer que o Ascendente é estimativa');
  assert.match(result, /00:00|00h|h 00|hora.*não.*inform|inform/i, 'aviso precisa mencionar a hora assumida/ausente');
  assert.ok(
    /troca de signo|mais sensível|hipotét|aproxim|não.*inform|inform/.test(result),
    'aviso precisa deixar claro que o Ascendente provavelmente NÃO é o real',
  );
}

async function testAscendantWarningRendersAdjacentToAscendant() {
  const app = boot({ pathname: '/' });
  await app.ready;
  app.queue(200, {
    ...SAMPLE_PREVIEW,
    ascendant: { sign: 'Áries', sign_label: 'Áries', degree: 4.2, text: 'Ascendente em Áries estimado.' },
    birth_time_approximate: true,
    birth_time_assumed: true,
    ascendant_warning: {
      'pt-BR': 'Estimativa brutal.',
      'es-AR': 'Estimación cruda.',
    },
  });
  await submit(app.window, fillForm(app.document, { ...VALID_INPUT, birth_time: '' }));

  const result = app.document.getElementById('preview-result');
  const ascendant = result.querySelector('[data-ascendant-card]');
  const warning = result.querySelector('[data-ascendant-warning]');
  assert.ok(ascendant, 'precisa existir o card do Ascendente no DOM');
  assert.ok(warning, 'precisa existir o aviso colado no Ascendente');
  // Adjacente: o warning vem logo DEPOIS do Ascendente na ordem do DOM.
  // linkedom não tem ``Node.DOCUMENT_POSITION_FOLLOWING``, então comparamos
  // a ordem por índice entre os filhos diretos do container.
  const siblings = Array.from(result.children);
  const ascIdx = siblings.indexOf(ascendant);
  const warnIdx = siblings.indexOf(warning);
  assert.ok(ascIdx >= 0, 'Ascendente precisa ser filho do resultado');
  assert.ok(warnIdx >= 0, 'aviso precisa ser filho do resultado');
  assert.equal(warnIdx, ascIdx + 1, 'aviso precisa estar logo abaixo do Ascendente, não em rodapé');
}

async function testAscendantWarningIsLocalizedEsAr() {
  const app = boot({ pathname: '/es' });
  await app.ready;
  app.queue(200, {
    ...SAMPLE_PREVIEW,
    locale: 'es-AR',
    ascendant: { sign: 'Aries', sign_label: 'Aries', degree: 4.2, text: 'Ascendente en Aries estimado.' },
    birth_time_approximate: true,
    birth_time_assumed: true,
    ascendant_warning: {
      'pt-BR':
        'Hora de nascimento não informada — assumimos 00:00 só para mostrar um valor. ESTIMATIVA.',
      'es-AR':
        'Hora de nacimiento no informada — asumimos 00:00 solo para mostrar un valor. ESTIMACIÓN.',
    },
  });
  await submit(app.window, fillForm(app.document, { ...VALID_INPUT, birth_time: '' }));

  const warning = app.document.querySelector('[data-ascendant-warning]');
  assert.ok(warning, 'aviso precisa aparecer na versão es-AR também');
  assert.match(warning.textContent, /ESTIMACIÓN|estimaci/i, 'aviso precisa estar traduzido para es-AR');
  assert.ok(!/ESTIMATIVA/.test(warning.textContent), 'aviso es-AR não pode vazar texto em pt-BR');
}

async function testSunAndMoonDoNotShowWarning() {
  const app = boot({ pathname: '/' });
  await app.ready;
  app.queue(200, {
    ...SAMPLE_PREVIEW,
    birth_time_approximate: true,
    birth_time_assumed: true,
    ascendant_warning: {
      'pt-BR': 'Aviso do Ascendente, não do Sol/Lua.',
      'es-AR': 'Aviso del Ascendente, no del Sol/Luna.',
    },
  });
  await submit(app.window, fillForm(app.document, { ...VALID_INPUT, birth_time: '' }));

  const result = app.document.getElementById('preview-result');
  const sunCard = result.querySelector('[data-luminary="sun"]');
  const moonCard = result.querySelector('[data-luminary="moon"]');
  assert.ok(sunCard && !sunCard.querySelector('[data-ascendant-warning]'), 'Sol não pode carregar o aviso do Ascendente');
  assert.ok(moonCard && !moonCard.querySelector('[data-ascendant-warning]'), 'Lua não pode carregar o aviso do Ascendente');
}

async function testWithBirthTimeNoWarning() {
  const app = boot({ pathname: '/' });
  await app.ready;
  app.queue(200, { ...SAMPLE_PREVIEW, birth_time_approximate: false, birth_time_assumed: false });
  await submit(app.window, fillForm(app.document, VALID_INPUT));

  const result = app.document.getElementById('preview-result');
  assert.equal(result.querySelector('[data-ascendant-warning]'), null, 'com hora informada, não tem aviso');
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
  testMissingBirthTimeShowsAscendantWithWarning,
  testAscendantWarningRendersAdjacentToAscendant,
  testAscendantWarningIsLocalizedEsAr,
  testSunAndMoonDoNotShowWarning,
  testWithBirthTimeNoWarning,
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
