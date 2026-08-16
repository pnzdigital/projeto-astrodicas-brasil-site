// Testes do default seguro no cadeado de trial (portal-demo/index.html).
//
// Quatro caminhos cobertos:
//   1. 200 com in_trial=true → BLOQUEADO
//   2. 200 com in_trial=false (pagante) → liberado conforme entitlement
//   3. 200 com subscription=null (sem assinatura, ex.: Cakto) → subscriptionResolved=true, inTrial=false → liberado
//   4. Erro de rede / 5xx → subscriptionResolved permanece false → inTrial=true → BLOQUEADO
//
// Roda com: ``node portal-demo/trial-lock-default.test.mjs``.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, 'index.html'), 'utf8');
const scriptMatch = html.match(/<script type="module">([\s\S]*?)<\/script>\s*<\/body>/);
const script = scriptMatch ? scriptMatch[1] : '';

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

// ── Estado inicial ────────────────────────────────────────────────────────────

test('appState começa com subscriptionResolved=false', () => {
  assert.match(script, /subscriptionResolved:\s*false/);
});

// ── Fórmula do gate ───────────────────────────────────────────────────────────

test('inTrial usa !subscriptionResolved || in_trial (default seguro via resolved=false)', () => {
  assert.match(script, /!appState\.subscriptionResolved\s*\|\|\s*Boolean\(appState\.subscription\?\.in_trial\)/);
});

test('cadeado de trial dispara quando inTrial=true ou entitlement.paid=false para TRIAL_LOCKED_CONTENT', () => {
  assert.match(script, /if \(TRIAL_LOCKED_CONTENT\.has\(item\.id\) && \(inTrial \|\| !entitlement\?\.paid\)\) item\.state = ACCESS_STATES\.locked/);
});

// ── Caminho 1: 200 com in_trial=true → BLOQUEADO ─────────────────────────────

test('[200 trial] .then() salva subscription e marca subscriptionResolved=true', () => {
  const thenMatch = script.match(/\.then\(\(data\) => \{([^}]+)\}\)/);
  assert.ok(thenMatch, '.then() da chamada de subscription não encontrado');
  const body = thenMatch[1];
  assert.match(body, /appState\.subscription\s*=\s*data\.subscription/);
  assert.match(body, /appState\.subscriptionResolved\s*=\s*true/);
  assert.match(body, /applyRemoteState\(\)/);
  // Com in_trial=true: inTrial = !true || true = true → gate bloqueia ✓
});

// ── Caminho 2: 200 com in_trial=false (pagante) → liberado ───────────────────

test('[200 pagante] subscriptionResolved=true + in_trial=false → inTrial=false → gate não bloqueia', () => {
  // Lógica: !true || Boolean(false) = false → gate não dispara → item segue entitlement
  const gateMatch = script.match(/const inTrial\s*=\s*([^;]+);/);
  assert.ok(gateMatch, 'const inTrial não encontrado');
  assert.match(gateMatch[1], /!appState\.subscriptionResolved/);
  assert.match(gateMatch[1], /appState\.subscription\?\.in_trial/);
  // Fórmula correta: !true || Boolean(false) = false ✓ (verificado por análise estática)
});

// ── Caminho 3: 200 com subscription=null (Cakto, sem assinatura) → liberado ──

test('[200 null] subscription=null chega no .then() → subscriptionResolved=true, inTrial=false', () => {
  // Backend retorna {"subscription": null} para usuários sem assinatura
  // .then() roda: subscription=null, subscriptionResolved=true
  // inTrial = !true || Boolean(null?.in_trial) = false || false = false → gate não bloqueia ✓
  const thenMatch = script.match(/\.then\(\(data\) => \{([^}]+)\}\)/);
  assert.ok(thenMatch, '.then() não encontrado');
  // subscription=null passa por data.subscription (null) — não tem tratamento especial
  assert.match(thenMatch[1], /appState\.subscription\s*=\s*data\.subscription/);
  assert.match(thenMatch[1], /appState\.subscriptionResolved\s*=\s*true/);
  // Confirma que o backend NUNCA retorna 404 nesse caso — a distinção é no JSON
  assert.match(script, /Backend sempre devolve 200 com subscription:null/);
});

// ── Caminho 4: erro de rede / 5xx → subscriptionResolved permanece false → BLOQUEADO

test('[falha de rede] .catch() NÃO marca subscriptionResolved=true — permanece false → inTrial=true → bloqueado', () => {
  const blockMatch = script.match(/apiRequest\('\/api\/me\/subscription'\)([\s\S]*?)\.catch\(\(\) => \{([^}]*)\}\)/);
  assert.ok(blockMatch, 'bloco apiRequest(/api/me/subscription) + .catch() não encontrado');
  const catchBody = blockMatch[2];
  // Deve chamar applyRemoteState para re-renderizar
  assert.match(catchBody, /applyRemoteState\(\)/);
  // NÃO deve marcar subscriptionResolved=true — senão inTrial vira false e libera
  assert.ok(
    !catchBody.includes('subscriptionResolved'),
    '.catch() não deve tocar subscriptionResolved — deve permanecer false (inTrial=true → bloqueado)',
  );
  // NÃO deve atribuir subscription (permanece null, mas irrelevante pois resolved=false já garante bloqueio)
  assert.ok(
    !catchBody.includes('appState.subscription'),
    '.catch() não deve atribuir appState.subscription',
  );
});

test('[falha de rede] comentário do .catch() descreve que subscriptionResolved permanece false', () => {
  assert.match(script, /subscriptionResolved permanece false/);
});

// ── Comentário descreve comportamento real ────────────────────────────────────

test('comentário documenta que .catch() é inconclusivo (rede/5xx), não "sem assinatura"', () => {
  assert.match(script, /inconclusivo/i);
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
