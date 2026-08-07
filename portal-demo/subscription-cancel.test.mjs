// Testes do cancelamento de assinatura no painel (portal-demo/index.html).
//
// Cobre: controle visível na área da conta (não escondido em texto cinza
// minúsculo), confirmação que declara acesso-até e "não cobra mais" antes de
// confirmar, estado honesto pós-cancelamento (cancelada + acesso até + "pra
// voltar, assina de novo"), caminho de erro sem botão morto, e paridade
// pt-BR/es-AR. Endpoint usado é o que a API já expõe: POST
// /api/me/subscription/cancel (ver api/app/subscriptions.py) — este teste
// não toca em api/, só lê o HTML/JS do front.
//
// Roda com: ``node portal-demo/subscription-cancel.test.mjs``.

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

// ── Controle visível, não escondido ─────────────────────────────────────────

test('existe um botão de cancelar assinatura na área da conta, não link cinza minúsculo', () => {
  const buttonMatch = html.match(/<button[^>]*id="cancel-subscription-trigger"[^>]*>/);
  assert.ok(buttonMatch, 'botão cancel-subscription-trigger não encontrado');
  assert.match(buttonMatch[0], /class="button/, 'botão de cancelar não usa a classe .button (visível), pode estar como texto discreto');
});

test('o botão de cancelar mora dentro do painel da conta (account-user), não enterrado em outro fluxo', () => {
  const accountMatch = html.match(/<div class="account-user" id="account-user"[\s\S]*?<\/div>\s*<\/div>\s*<\/dialog>/);
  assert.ok(accountMatch, 'bloco account-user não encontrado');
  assert.ok(accountMatch[0].includes('cancel-subscription-trigger'), 'botão de cancelar não está dentro de account-user');
});

test('bloco de assinatura só aparece quando existe subscription (some pra quem comprou pelo Cakto)', () => {
  assert.match(script, /if \(!sub\) \{ block\.hidden = true; return; \}/);
});

// ── Confirmação declara a verdade antes de cancelar ─────────────────────────

test('abrir o diálogo de cancelamento preenche a data de acesso e a mensagem de "não cobra mais" antes de confirmar', () => {
  assert.match(script, /function fillCancelDialog\(\)/);
  assert.match(script, /nada mais será cobrado depois disso/);
  assert.match(script, /no se te va a cobrar de nuevo/);
});

test('diálogo de cancelamento tem opção de manter a assinatura, não só confirmar', () => {
  assert.match(html, /id="cancel-keep"[^>]*>Manter minha assinatura/);
});

// ── Chamada real ao endpoint que pane-1012 confirmou ────────────────────────

test('confirmar cancelamento chama POST /api/me/subscription/cancel, o único endpoint usado', () => {
  assert.match(script, /apiRequest\('\/api\/me\/subscription\/cancel', \{ method: 'POST', body: '\{\}' \}\)/);
  assert.ok(!/\/api\/me\/subscription\/pause/.test(script), 'endpoint inventado (pause) não deveria existir');
});

test('painel também busca GET /api/me/subscription pra saber o estado atual', () => {
  assert.match(script, /apiRequest\('\/api\/me\/subscription'\)/);
});

// ── Estado pós-cancelamento é honesto ───────────────────────────────────────

test('depois de cancelar, mostra "cancelada + acesso até" e deixa claro que voltar exige assinar de novo', () => {
  assert.match(script, /Cancelada\$\{accessUntil[\s\S]{0,40}acesso até/);
  assert.match(script, /precisa assinar de novo/);
  assert.match(script, /tenés que suscribirte de nuevo/);
});

test('sucesso do cancelamento esconde o botão de cancelar de novo (não dá pra cancelar duas vezes)', () => {
  const match = script.match(/if \(sub\.status === 'cancelled'\) \{([\s\S]*?)\n\s*\}/);
  assert.ok(match, 'ramo de status cancelled não encontrado em renderSubscriptionBlock');
  assert.match(match[1], /trigger\.hidden = true/);
});

// ── Falha no endpoint nunca vira botão morto ────────────────────────────────

test('se o cancelamento falhar, mostra erro e reabilita o botão — nunca trava sem saída', () => {
  const match = script.match(/async function confirmCancelSubscription\(\)\s*\{([\s\S]*?)\n    \}/);
  assert.ok(match, 'confirmCancelSubscription não encontrada');
  const body = match[1];
  assert.match(body, /catch \(error\)/);
  assert.match(body, /confirmButton\.disabled = false/);
  assert.match(body, /setCancelError\(/);
});

test('mensagem de erro do cancelamento dá outro caminho pra sair (link de suporte), não fica sem saída', () => {
  assert.match(script, /href="\/suporte">fale com o suporte<\/a>/);
  assert.match(script, /href="\/es\/suporte">escribinos a soporte<\/a>/);
});

// ── Paridade pt-BR / es-AR ──────────────────────────────────────────────────

test('copy do cancelamento existe nos dois locales (pt-BR direto no script, es-AR via IS_ARGENTINA e dicionário de tradução)', () => {
  assert.match(script, /Cancelar sua assinatura\?/);
  assert.match(html, /'Cancelar sua assinatura\?': '¿Cancelar tu suscripción\?'/);
  assert.match(html, /'Cancelar assinatura': 'Cancelar suscripción'/);
});

// ── Runner ───────────────────────────────────────────────────────────────

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
