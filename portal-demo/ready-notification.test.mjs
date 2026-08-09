// Trava end-to-end: quando uma leitura assíncrona (202 + polling) termina,
// o portal precisa avisar a cliente NA HORA, sem recarregar a página, sem
// duplicar aviso pro mesmo conteúdo e sem repetir o clique/POST de geração
// enquanto já está em andamento.
//
// Roda o <script type="module"> real de index.html num DOM linkedom (mesmo
// padrão de storefront-preview.test.mjs), simula sessão logada + entitlement
// liberado, clica no card da leitura e segue o poll de verdade até o status
// virar 'ready'.
//
// Roda com: ``node portal-demo/ready-notification.test.mjs``.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve as pathResolve } from 'node:path';
import assert from 'node:assert/strict';
import { parseHTML } from 'linkedom';

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(pathResolve(here, 'index.html'), 'utf8');

const CONTENT_ID = 'site:content:mapa_astral_completo';
const PRODUCT_ID = 'site:mapa_astral';

function boot(initialResponses) {
  const { window } = parseHTML(html);
  const document = window.document;
  const fetchCalls = [];
  // O script chama bootstrap() na última linha do módulo, que dispara
  // fetch('/api/session') de forma SÍNCRONA (até o primeiro await real)
  // durante a própria execução do script — antes que o teste tenha chance
  // de chamar queue(). Por isso as respostas do boot inicial (session,
  // profile, access, readings) precisam estar na fila ANTES de instanciar
  // o script, não depois.
  const fetchResponses = (initialResponses || []).map(([status, payload]) => ({
    ok: status >= 200 && status < 300, status, json: async () => payload,
  }));

  window.scrollTo = () => {};
  window.print = () => {};
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  window.history = { pushState() {}, replaceState() {} };
  window.CSS = { escape: (s) => String(s).replace(/[^a-zA-Z0-9_-]/g, (c) => `\\${c}`) };
  Object.defineProperty(window, 'location', {
    value: { pathname: '/', hostname: 'astrodicas.pnzdigital.com.br', origin: 'https://astrodicas.pnzdigital.com.br', search: '', hash: '', href: '/', assign() {} },
    writable: true,
  });
  // linkedom não implementa <dialog>.showModal/close — só precisamos que não
  // explodam quando chamados incidentalmente (o fluxo testado não abre dialog).
  document.querySelectorAll('dialog').forEach((d) => {
    d.showModal = d.showModal || function () { this.open = true; };
    d.close = d.close || function () { this.open = false; };
  });
  window.Element.prototype.scrollIntoView = window.Element.prototype.scrollIntoView || function () {};

  window.fetch = async (url, opts = {}) => {
    fetchCalls.push({ url, opts });
    if (!fetchResponses.length) throw new Error(`fetch sem resposta enfileirada: ${url}`);
    return fetchResponses.shift();
  };

  const stubConfig = { default: { content: [], checkout: { defaultProvider: 'gg_checkout', providers: {} } } };
  window.__STUB_CONFIG__ = stubConfig;

  const scriptMatch = html.match(/<script type="module">([\s\S]*?)<\/script>\s*<\/body>/);
  if (!scriptMatch) throw new Error('module script não encontrado em index.html');
  let body = scriptMatch[1];
  body = body.replace(
    /const \{ default: PORTAL_CONFIG \} = await import\(CONFIG_URL\);/,
    'const { default: PORTAL_CONFIG } = window.__STUB_CONFIG__;',
  );
  assert.ok(body.includes('window.__STUB_CONFIG__'), 'stub de PORTAL_CONFIG não foi injetado — import dinâmico mudou de forma?');

  const run = new window.Function('window', 'document', `return (async()=>{${body}})()`);
  return {
    window,
    document,
    fetchCalls,
    queue(status, payload) {
      fetchResponses.push({ ok: status >= 200 && status < 300, status, json: async () => payload });
    },
    ready: run(window, document),
  };
}

async function tick(ms) {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

function clickReadCard(window, document, contentId) {
  const button = document.querySelector(`[data-read="${contentId}"]`);
  assert.ok(button, `botão data-read="${contentId}" não encontrado no card`);
  button.dispatchEvent(new window.Event('click', { bubbles: true, cancelable: true }));
}

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

const INITIAL_SESSION_RESPONSES = [
  [200, { authenticated: true, user: { name: 'Ana' } }],
  [200, { profile: { birth_date: '1990-01-01', birth_city: 'Recife' } }],
  [200, { entitlements: [{ product_id: PRODUCT_ID, status: 'available' }] }],
  [200, { readings: [] }],
];

test('leitura que fica pronta gera exatamente um aviso, com título e CTA pra abrir', async () => {
  const app = boot(INITIAL_SESSION_RESPONSES);
  await app.ready;
  await tick(60);

  const { document } = app;
  const notifs = document.getElementById('ready-notifications');
  assert.ok(notifs, 'container #ready-notifications ausente');
  assert.equal(notifs.children.length, 0, 'não deveria ter aviso antes de gerar nada');

  // Clica no card: dispara POST /generate (202 in_progress) + poll fire-and-forget.
  app.queue(200, { reading: { content_id: CONTENT_ID, status: 'in_progress' } });
  clickReadCard(app.window, document, CONTENT_ID);
  await tick(60);

  // Primeiro poll ainda em andamento.
  app.queue(200, { readings: [{ content_id: CONTENT_ID, status: 'in_progress' }] });
  await tick(2600); // GENERATE_POLL_INTERVAL_MS = 2500

  assert.equal(notifs.children.length, 0, 'não deveria avisar enquanto ainda está in_progress');

  // Segundo poll: pronto.
  app.queue(200, { readings: [{ content_id: CONTENT_ID, status: 'ready', body_html: '<p>Seu mapa.</p>' }] });
  await tick(2600);

  assert.equal(notifs.children.length, 1, `esperava exatamente 1 aviso, achou ${notifs.children.length}`);
  const notif = notifs.children[0];
  assert.equal(notif.dataset.readyFor, CONTENT_ID, 'aviso não referencia o content_id certo');
  assert.match(notif.textContent, /Mapa Astral Completo/, 'aviso não diz qual leitura ficou pronta');
  const openBtn = notif.querySelector('[data-read]');
  assert.ok(openBtn, 'aviso sem botão pra abrir a leitura direto');
  assert.equal(openBtn.dataset.read, CONTENT_ID);
});

test('reabrir a leitura em progresso (2ª aba/rota) não dispara 2º POST nem 2º aviso — guard de pollingContentIds', async () => {
  const app = boot(INITIAL_SESSION_RESPONSES);
  await app.ready;
  await tick(60);

  const { window, document, fetchCalls } = app;

  app.queue(200, { reading: { content_id: CONTENT_ID, status: 'in_progress' } });
  clickReadCard(window, document, CONTENT_ID);
  await tick(60);

  const generateCallsAfterFirstClick = fetchCalls.filter((c) => c.url.includes('/generate')).length;
  assert.equal(generateCallsAfterFirstClick, 1, 'primeiro clique deveria disparar 1 POST /generate');

  // Simula uma 2ª aba/rota pedindo a MESMA leitura enquanto ela ainda está
  // em geração (ex: hash direto pra #/leitura/<id>). openReadingById chama
  // generateReading de novo — o guard de pollingContentIds precisa barrar
  // um 2º POST e só levar o usuário pra tela de progresso.
  window.location.hash = `#/leitura/${encodeURIComponent(CONTENT_ID)}`;
  window.dispatchEvent(new window.Event('hashchange'));
  await tick(60);
  const generateCallsAfterSecondRoute = fetchCalls.filter((c) => c.url.includes('/generate')).length;
  assert.equal(generateCallsAfterSecondRoute, 1, '2ª rota pra leitura em geração disparou novo POST /generate — guard de pollingContentIds falhou');

  // Usuário sai da tela de progresso e volta pro dashboard antes de terminar
  // — é o caminho normal (ele não fica preso vendo a barra de progresso).
  document.querySelector('[data-back-to-library]')?.dispatchEvent(new window.Event('click', { bubbles: true, cancelable: true }));
  await tick(20);

  app.queue(200, { readings: [{ content_id: CONTENT_ID, status: 'ready', body_html: '<p>Seu mapa.</p>' }] });
  await tick(2600);

  const notifs = document.getElementById('ready-notifications');
  assert.equal(notifs.children.length, 1, `esperava exatamente 1 aviso após a leitura ficar pronta, achou ${notifs.children.length}`);
  assert.equal(fetchCalls.filter((c) => c.url.includes('/generate')).length, 1, 'ao final, só deveria ter existido 1 POST /generate no total');
});

let failed = 0;
for (const [name, fn] of tests) {
  try {
    await fn();
    console.log(`ok - ${name}`);
  } catch (err) {
    failed++;
    console.error(`not ok - ${name}`);
    console.error(err.stack || err.message);
  }
}
console.log(`\n${tests.length - failed}/${tests.length} passed`);
// setTimeout de auto-dismiss (30s) e loops de poll pendentes ficariam
// segurando o event loop — o teste já terminou, força a saída.
process.exit(failed > 0 ? 1 : 0);
