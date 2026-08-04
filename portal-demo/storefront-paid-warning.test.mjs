// Aviso pré-compra no fluxo pago: a storefront não tem checkout-form próprio
// (Cakto e Mercado Pago cuidam do checkout), mas o cliente FORMA a expectativa
// de compra dentro da própria storefront ao clicar no CTA "Ver meu Mapa Astral".
//
// Quando a prévia grátis rodou com hora ausente (assumindo 00:00), o bloco de
// upsell que conduz à compra tem que carregar o mesmo aviso do card do
// Ascendente — colado, destacado, junto do CTA — pra não pegar o cliente de
// surpresa: "ah, o Ascendente do mapa completo é ESTIMADO, não exato".
//
// Estes testes rodam o <script type="module"> inline de storefront.html num
// DOM linkedom, com a API devolvendo ``birth_time_assumed: true``, e verificam
// que o aviso é renderizado dentro de ``#preview-upsell`` antes do botão.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve as pathResolve } from 'node:path';
import assert from 'node:assert/strict';
import { parseHTML } from 'linkedom';

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(pathResolve(here, 'storefront.html'), 'utf8');

const SAMPLE_PREVIEW_ASSUMED = {
  locale: 'pt-BR',
  locked: true,
  birth_time_approximate: true,
  birth_time_assumed: true,
  sun: { sign: 'Touro', sign_label: 'Touro', degree: 29.4, text: 'Texto do Sol em Touro para a prévia.' },
  moon: { sign: 'Leão', sign_label: 'Leão', degree: 3.1, text: 'Texto da Lua em Leão para a prévia.' },
  ascendant: { sign: 'Áries', sign_label: 'Áries', degree: 4.2, text: 'Ascendente em Áries estimado.' },
  ascendant_warning: {
    'pt-BR':
      'Hora de nascimento não informada — assumimos 00:00 só para mostrar um valor. O Ascendente é o ponto mais sensível à hora do mapa inteiro (troca de signo a cada ~2h), então este resultado é uma ESTIMATIVA.',
    'es-AR':
      'Hora de nacimiento no informada — asumimos 00:00 solo para mostrar un valor. El Ascendente es el punto más sensible a la hora de toda la carta (cambia de signo cada ~2h), así que este resultado es una ESTIMACIÓN.',
  },
  planets: [
    { name: 'Sol', label: 'Sol', sign: 'Touro', sign_label: 'Touro', degree: 29.4, retrograde: false },
    { name: 'Lua', label: 'Lua', sign: 'Leão', sign_label: 'Leão', degree: 3.1, retroactive: false, retrograde: false },
  ],
};

const SAMPLE_PREVIEW_KNOWN = {
  ...SAMPLE_PREVIEW_ASSUMED,
  ascendant: { sign: 'Libra', sign_label: 'Libra', degree: 12.8, text: 'Texto do Ascendente em Libra.' },
  birth_time_approximate: false,
  birth_time_assumed: false,
};

// ── Harness (mesmo padrão de storefront-preview.test.mjs) ───────────────────

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
  birth_time: '',
  birth_city: 'Recife',
};

// ── Testes ──────────────────────────────────────────────────────────────────

async function testUpsellCarriesAscendantWarningWhenTimeWasAssumed() {
  // Pré-compra: o cliente viu a prévia com hora ausente e está prestes a
  // clicar em "Ver meu Mapa Astral". O aviso de Ascendente estimado tem que
  // aparecer JUNTO desse CTA, não só no card do Ascendente lá em cima —
  // separar os dois esconderia a incerteza na hora que importa.
  const app = boot({ pathname: '/' });
  await app.ready;
  app.queue(200, SAMPLE_PREVIEW_ASSUMED);
  await submit(app.window, fillForm(app.document, VALID_INPUT));

  const upsell = app.document.getElementById('preview-upsell');
  assert.ok(upsell, 'bloco de upsell precisa existir após a prévia');
  const warning = upsell.querySelector('[data-upsell-ascendant-warning]');
  assert.ok(warning, 'aviso do Ascendente estimado precisa estar dentro do upsell, colado no CTA pré-compra');
  assert.match(warning.textContent, /Ascendente estimado|estimativa|estimado/i);
  assert.match(warning.textContent, /00:00|hora.*não.*inform|inform/i);
}

async function testUpsellWarningIsAdjacentToCta() {
  // Mesma regra visual do card do Ascendente: aviso logo antes do CTA, não
  // em rodapé nem dissociado do botão. O cliente bate o olho e vê os dois
  // juntos.
  const app = boot({ pathname: '/' });
  await app.ready;
  app.queue(200, SAMPLE_PREVIEW_ASSUMED);
  await submit(app.window, fillForm(app.document, VALID_INPUT));

  const upsell = app.document.getElementById('preview-upsell');
  const warning = upsell.querySelector('[data-upsell-ascendant-warning]');
  const cta = upsell.querySelector('a[href], button');
  assert.ok(warning && cta, 'aviso e CTA precisam existir no upsell');
  const upsellChildren = Array.from(upsell.children);
  const warnIdx = upsellChildren.indexOf(warning);
  const ctaIdx = upsellChildren.indexOf(cta);
  assert.ok(warnIdx >= 0 && ctaIdx >= 0, 'aviso e CTA precisam ser filhos do upsell');
  assert.ok(warnIdx < ctaIdx, 'aviso precisa estar ANTES do CTA — não abaixo nem dissociado');
}

async function testUpsellWarningLocalizedEsAr() {
  // Cliente es-AR vê o aviso em espanhol, não em pt-BR traduzido na mão.
  const app = boot({ pathname: '/es' });
  await app.ready;
  app.queue(200, { ...SAMPLE_PREVIEW_ASSUMED, locale: 'es-AR' });
  await submit(app.window, fillForm(app.document, VALID_INPUT));

  const warning = app.document
    .getElementById('preview-upsell')
    .querySelector('[data-upsell-ascendant-warning]');
  assert.ok(warning, 'aviso precisa aparecer na versão es-AR também');
  assert.match(warning.textContent, /ESTIMACIÓN|estimaci/i, 'aviso es-AR precisa estar traduzido');
  assert.ok(
    !/ESTIMATIVA/.test(warning.textContent),
    'aviso es-AR não pode vazar texto em pt-BR',
  );
}

async function testUpsellNoWarningWhenBirthTimeIsKnown() {
  // Com hora informada, não tem aviso em lugar nenhum — nem no card do
  // Ascendente nem no upsell pré-compra. Cliente que deu hora completa não
  // merece ler "estimativa" onde não há.
  const app = boot({ pathname: '/' });
  await app.ready;
  app.queue(200, SAMPLE_PREVIEW_KNOWN);
  await submit(app.window, fillForm(app.document, {
    birth_date: '1990-05-20', birth_time: '14:30', birth_city: 'Recife',
  }));

  const upsell = app.document.getElementById('preview-upsell');
  assert.equal(
    upsell.querySelector('[data-upsell-ascendant-warning]'),
    null,
    'com hora informada, o upsell não carrega aviso',
  );
}

async function testUpsellWarningUsesLocalizedTextFromApi() {
  // O texto do aviso vem da API (já localizado no backend pt-BR/es-AR),
  // NÃO é reescrito no front. Mesma política do card Ascendente da prévia.
  // Evitar divergência entre cópias diferentes do mesmo aviso.
  const customWarningPt = 'Mensagem customizada PT vinda do backend — XYZ.';
  const app = boot({ pathname: '/' });
  await app.ready;
  app.queue(200, {
    ...SAMPLE_PREVIEW_ASSUMED,
    ascendant_warning: { 'pt-BR': customWarningPt, 'es-AR': 'custom ES' },
  });
  await submit(app.window, fillForm(app.document, VALID_INPUT));

  const warning = app.document
    .getElementById('preview-upsell')
    .querySelector('[data-upsell-ascendant-warning]');
  assert.ok(warning);
  assert.ok(
    warning.textContent.includes('XYZ'),
    `upsell precisa usar o texto da API, nao o default do front. Recebido: ${warning.textContent}`,
  );
}

const tests = [
  testUpsellCarriesAscendantWarningWhenTimeWasAssumed,
  testUpsellWarningIsAdjacentToCta,
  testUpsellWarningLocalizedEsAr,
  testUpsellNoWarningWhenBirthTimeIsKnown,
  testUpsellWarningUsesLocalizedTextFromApi,
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
  console.error(`storefront paid warning: ${tests.length - failed}/${tests.length} ok, ${failed} falhando`);
  process.exit(1);
}
console.log(`storefront paid warning: ${tests.length}/${tests.length} ok`);
