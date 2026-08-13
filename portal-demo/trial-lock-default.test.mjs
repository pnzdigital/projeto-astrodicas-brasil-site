// Testes do default seguro no cadeado de trial (portal-demo/index.html).
//
// Três estados cobertos:
//   1. Assinatura ainda não resolvida (subscriptionResolved=false) → BLOQUEADO
//   2. Assinatura resolvida com in_trial=true → BLOQUEADO
//   3. Assinatura resolvida com in_trial=false (pagante) → liberado conforme entitlement
//
// Inclui explicitamente o caso de falha da chamada → BLOQUEADO.
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

// ── Gate usa subscriptionResolved ─────────────────────────────────────────────

test('inTrial é true quando subscriptionResolved=false (ainda carregando)', () => {
  assert.match(script, /!appState\.subscriptionResolved\s*\|\|\s*Boolean\(appState\.subscription\?\.in_trial\)/);
});

test('cadeado de trial dispara quando inTrial=true para TRIAL_LOCKED_CONTENT', () => {
  assert.match(script, /if \(inTrial && TRIAL_LOCKED_CONTENT\.has\(item\.id\)\) item\.state = ACCESS_STATES\.locked/);
});

// ── .then() marca resolved e passa subscription ───────────────────────────────

test('.then() marca subscriptionResolved=true antes de chamar applyRemoteState', () => {
  const thenMatch = script.match(/\.then\(\(data\) => \{([\s\S]*?)\}\)/);
  assert.ok(thenMatch, '.then() da chamada de subscription não encontrado');
  const thenBody = thenMatch[1];
  assert.match(thenBody, /appState\.subscriptionResolved\s*=\s*true/);
  assert.match(thenBody, /appState\.subscription\s*=\s*data\.subscription/);
  assert.match(thenBody, /applyRemoteState\(\)/);
});

// ── .catch() marca resolved mas mantém subscription=null → inTrial=true ──────

test('.catch() marca subscriptionResolved=true (falha da chamada resulta em BLOQUEADO)', () => {
  // Busca o .catch() que vem depois do apiRequest('/api/me/subscription')
  const blockMatch = script.match(/apiRequest\('\/api\/me\/subscription'\)([\s\S]*?)\.catch\(\(\) => \{([^}]*)\}\)/);
  assert.ok(blockMatch, 'bloco apiRequest(/api/me/subscription) + .catch() não encontrado');
  const catchBody = blockMatch[2];
  assert.match(catchBody, /appState\.subscriptionResolved\s*=\s*true/);
  assert.match(catchBody, /applyRemoteState\(\)/);
  // subscription NÃO é atribuído no catch → permanece null → inTrial=true
  assert.ok(
    !catchBody.includes('appState.subscription ='),
    'catch não deve atribuir appState.subscription — deve permanecer null para garantir bloqueio',
  );
});

// ── Comentários descrevem o comportamento real ───────────────────────────────

test('comentário próximo ao .catch() descreve que subscription=null resulta em bloqueio', () => {
  assert.match(script, /subscription=null.*bloqueado|bloqueado.*subscription=null/i);
});

test('comentário documenta o princípio do default seguro (não descreve comportamento falso)', () => {
  assert.match(script, /default seguro/i);
  // Comentário antigo que mentia (inTrial=false com null) não pode estar presente
  assert.ok(
    !script.includes('inTrial=false e TRIAL_LOCKED_CONTENT não precisa mudar'),
    'comentário falso ainda presente — descreve comportamento que não existe mais',
  );
});

// ── Pagante (in_trial=false) ──────────────────────────────────────────────────

test('quando subscriptionResolved=true e in_trial=false, inTrial é false e gate não bloqueia', () => {
  // Verifica a fórmula: !false || Boolean(false) = false
  // Só podemos checar a lógica via análise estática do código fonte
  const gateMatch = script.match(/const inTrial\s*=\s*(!appState\.subscriptionResolved[^;]+);/);
  assert.ok(gateMatch, 'linha const inTrial não encontrada');
  // Expressão deve ser: !subscriptionResolved || Boolean(in_trial)
  // Quando resolved=true e in_trial=false: !true || Boolean(false) = false || false = false ✓
  assert.match(gateMatch[1], /!appState\.subscriptionResolved/);
  assert.match(gateMatch[1], /appState\.subscription\?\.in_trial/);
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
