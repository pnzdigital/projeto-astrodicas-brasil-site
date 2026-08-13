// Testes estáticos do pré-preenchimento de sessão no checkout.html.
//
// Caminhos cobertos:
//   1. Logado: banner visível, campos ocultos, POST envia use_session:true
//   2. Logado → "usar outro e-mail": campos voltam, aviso de entrega exibido
//   3. Deslogado: campos visíveis, POST envia use_session:false / email+name
//
// Roda com: ``node portal-demo/checkout-session-prefill.test.mjs``

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, 'checkout.html'), 'utf8');
const scriptMatch = html.match(/<script>([\s\S]*?)<\/script>\s*<\/body>/);
const script = scriptMatch ? scriptMatch[1] : '';

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

// ── Estrutura do DOM ──────────────────────────────────────────────────────────

test('session-banner existe no HTML com id correto', () => {
  assert.match(html, /id="session-banner"/);
});

test('session-who existe no HTML', () => {
  assert.match(html, /id="session-who"/);
});

test('switch-link existe no HTML', () => {
  assert.match(html, /id="switch-link"/);
});

test('other-email-warning existe no HTML', () => {
  assert.match(html, /id="other-email-warning"/);
});

test('session-banner começa hidden', () => {
  assert.match(html, /id="session-banner"[^>]*hidden/);
});

// ── Consulta /api/session ─────────────────────────────────────────────────────

test('fetch /api/session é chamado no carregamento', () => {
  assert.match(script, /fetch\(['"]\/api\/session['"]/);
});

test('fetch /api/session usa credentials same-origin', () => {
  assert.match(script, /credentials:\s*['"]same-origin['"]/);
});

test('.then() verifica data.authenticated e data.user', () => {
  assert.match(script, /data\.authenticated.*data\.user/s);
});

// ── Modo logado: applySessionMode ────────────────────────────────────────────

test('applySessionMode define useSession = true', () => {
  assert.match(script, /useSession\s*=\s*true/);
});

test('applySessionMode oculta fieldName e fieldEmail', () => {
  assert.match(script, /fieldName\.hidden\s*=\s*true/);
  assert.match(script, /fieldEmail\.hidden\s*=\s*true/);
});

test('applySessionMode remove required dos campos', () => {
  assert.match(script, /removeAttribute\(['"]required['"]\)/);
});

test('applySessionMode exibe sessionBanner', () => {
  assert.match(script, /sessionBanner\.hidden\s*=\s*false/);
});

// ── Modo "usar outro e-mail": applyOtherEmailMode ────────────────────────────

test('applyOtherEmailMode define useSession = false', () => {
  assert.match(script, /useSession\s*=\s*false/);
});

test('applyOtherEmailMode exibe fieldName e fieldEmail', () => {
  assert.match(script, /fieldName\.hidden\s*=\s*false/);
  assert.match(script, /fieldEmail\.hidden\s*=\s*false/);
});

test('applyOtherEmailMode exibe otherEmailWarning', () => {
  assert.match(script, /otherEmailWarning\.hidden\s*=\s*false/);
});

test('switchLink click aciona applyOtherEmailMode', () => {
  assert.match(script, /switchLink\.addEventListener\(['"]click['"]/);
  assert.match(script, /applyOtherEmailMode\(\)/);
});

// ── Texto bilíngue ────────────────────────────────────────────────────────────

test('copy pt-BR tem buyingAs', () => {
  assert.match(script, /buyingAs.*Comprando como/s);
});

test('copy es-AR tem buyingAs', () => {
  const esBlock = script.match(/'es-AR':\s*\{([\s\S]*?)\},\s*'pt-BR'/);
  assert.ok(esBlock, 'bloco es-AR não encontrado');
  assert.match(esBlock[1], /buyingAs/);
});

test('copy pt-BR tem switchLink "Usar outro e-mail"', () => {
  assert.match(script, /switchLink.*Usar outro e-mail/s);
});

test('copy pt-BR tem otherEmailWarning com referência ao e-mail da conta', () => {
  assert.match(script, /otherEmailWarning.*e-mail.*conta/s);
});

test('copy es-AR tem otherEmailWarning', () => {
  const esBlock = script.match(/'es-AR':\s*\{([\s\S]*?)\},\s*'pt-BR'/);
  assert.ok(esBlock, 'bloco es-AR não encontrado');
  assert.match(esBlock[1], /otherEmailWarning/);
});

// ── Submit logado: use_session:true ──────────────────────────────────────────

test('submit logado envia use_session: true e sem email no body', () => {
  assert.match(script, /use_session:\s*true/);
  assert.match(script, /useSession.*use_session/s);
});

test('submit deslogado envia use_session: false com email e name', () => {
  assert.match(script, /use_session:\s*false/);
});

test('fetch /api/checkout/order usa credentials same-origin', () => {
  const orderFetch = script.match(/fetch\(['"]\/api\/checkout\/order['"][^)]*\{([\s\S]*?)\}\s*\)/);
  assert.ok(orderFetch, 'fetch /api/checkout/order não encontrado');
  assert.match(orderFetch[0], /credentials:\s*['"]same-origin['"]/);
});

// ── Runner ────────────────────────────────────────────────────────────────────

let failed = 0;
for (const [name, fn] of tests) {
  try {
    fn();
    console.log(`✓ ${name}`);
  } catch (error) {
    failed += 1;
    console.error(`✗ ${name}`);
    console.error(`  ${error.message}`);
  }
}
console.log(`\n${tests.length - failed}/${tests.length} passaram`);
if (failed > 0) process.exit(1);
