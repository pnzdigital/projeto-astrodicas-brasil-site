// Testes do horóscopo diário e do guia do mês na área da assinante
// (portal-demo/index.html).
//
// Cobre: hero + card "seu momento agora" ligados ao content_id real
// (site:content:horoscopo_diario), geração automática ao abrir o painel
// (POST assíncrono 202 + polling em /api/me/readings), estados honestos
// (sem dados de nascimento, sem Plano Lua ativo, gerando, falha real) e
// o mês do guia calculado a partir da data, nunca fixo.
//
// Roda com: ``node portal-demo/daily-horoscope.test.mjs``.

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

// ── O dialog estático fake foi removido, o CTA aponta pro conteúdo real ────

test('dialog estático "horoscopo-dialog" (texto fixo de 29 de julho) foi removido', () => {
  assert.ok(!/id="horoscopo-dialog"/.test(html), 'dialog#horoscopo-dialog ainda existe com texto fixo');
});

test('CTA do hero e do card diário usam data-daily-action, não abrem mais o dialog estático', () => {
  assert.match(html, /id="hero-cta" data-daily-action="open"/);
  assert.match(html, /id="daily-cta" data-daily-action="open"/);
  assert.ok(!/data-dialog="horoscopo-dialog"/.test(html));
});

test('clique em [data-daily-action] chama handleDailyAction', () => {
  assert.match(script, /target\.matches\('\[data-daily-action\]'\)\)\s*handleDailyAction\(target\.dataset\.dailyAction\);/);
});

// ── Geração automática ao abrir o painel (202 + polling, sem clique) ───────

test('ensureDailyHoroscope roda sozinha depois do refresh de dados privados (dia 2 sem conteúdo deixa de existir)', () => {
  const match = script.match(/async function refreshPrivateData\(\)\s*\{[\s\S]*?\n    \}/);
  assert.ok(match, 'refreshPrivateData não encontrada');
  assert.ok(match[0].includes('void ensureDailyHoroscope();'), 'refreshPrivateData não dispara ensureDailyHoroscope');
});

test('ensureDailyHoroscope chama o mesmo endpoint genérico POST /api/me/readings/{content_id}/generate para o horóscopo diário', () => {
  assert.match(script, /async function ensureDailyHoroscope\(\)\s*\{[\s\S]*?apiRequest\(`\/api\/me\/readings\/\$\{encodeURIComponent\(item\.id\)\}\/generate`, \{ method: 'POST', body: '\{\}' \}\)/);
});

test('resposta in_progress do 202 assíncrono entra em polling silencioso (pollDailyHoroscope), sem travar nem mostrar erro', () => {
  assert.match(script, /async function pollDailyHoroscope\(item\)\s*\{[\s\S]*?apiRequest\('\/api\/me\/readings'\)/);
  assert.match(script, /if \(data\.reading\?\.status === 'in_progress'\) pollDailyHoroscope\(item\);/);
});

// ── Estados honestos, nunca caixa em branco ────────────────────────────────

test('sem dados de nascimento: pede pra completar o perfil, não mostra leitura quebrada', () => {
  const match = script.match(/function renderDailyHoroscope\(\)\s*\{[\s\S]*?\n    \}/);
  assert.ok(match, 'renderDailyHoroscope não encontrada');
  assert.ok(match[0].includes('if (!profileComplete()) {'), 'não checa profileComplete antes de gerar');
  assert.ok(match[0].includes("dailyAction = 'profile'"), 'não direciona pro fluxo de completar perfil');
});

test('sem Plano Lua ativo: explica o que o plano libera em vez de esconder ou mostrar erro', () => {
  const match = script.match(/function renderDailyHoroscope\(\)\s*\{[\s\S]*?\n    \}/);
  assert.ok(match[0].includes("entitlement.status !== 'available'") || match[0].includes('!entitlement'), 'não checa entitlement do Plano Lua');
  assert.ok(match[0].includes("dailyAction = 'plan'"), 'não oferece caminho pra conhecer o Plano Lua');
});

test('gerando (in_progress ou sem reading ainda): avisa que está gerando, botão fica aria-busy', () => {
  const match = script.match(/function renderDailyHoroscope\(\)\s*\{[\s\S]*?\n    \}/);
  assert.ok(match[0].includes("reading.status === 'in_progress'"), 'não trata estado in_progress');
  assert.ok(match[0].includes("toggleAttribute('aria-busy'"), 'botão não fica aria-busy durante a geração');
});

test('falha real de geração (fallback sem conteúdo nenhum): avisa e oferece tentar de novo, nunca fica em branco', () => {
  const match = script.match(/function renderDailyHoroscope\(\)\s*\{[\s\S]*?\n    \}/);
  assert.ok(match[0].includes("reading.status === 'fallback' && !hasContent"), 'não distingue falha real de fallback editorial com conteúdo');
  assert.ok(match[0].includes("dailyAction = 'retry'"), 'não oferece retry na falha real');
  const handler = script.match(/async function handleDailyAction\(action\)\s*\{[\s\S]*?\n    \}/);
  assert.ok(handler[0].includes("action === 'retry'"), "handleDailyAction não trata a ação 'retry'");
});

test('leitura pronta (ready ou fallback editorial com conteúdo) mostra um resumo real, extraído do body_html', () => {
  assert.match(script, /function excerptFromHtml\(html, maxLen = 220\)/);
  const match = script.match(/function renderDailyHoroscope\(\)\s*\{[\s\S]*?\n    \}/);
  assert.ok(match[0].includes('excerptFromHtml(reading.body_html'), 'não usa o body_html real da leitura no resumo');
});

// ── Guia do mês: mês calculado, nunca fixo ("Julho" hardcoded) ─────────────

test('meta do guia do mês é calculada a partir da data, não fica presa em "Julho"', () => {
  assert.match(script, /function monthLabelFor\(dateLike\)/);
  assert.match(script, /item\.meta = `\$\{monthLabelFor\(reading\?\.created_at\)\} · 12 min`;/);
});

// ── Map bug: guarda o mais recente por content_id ───────────────────────────

test('refreshPrivateData guarda todas as leituras em appState.allReadings', () => {
  const match = script.match(/async function refreshPrivateData\(\)\s*\{[\s\S]*?\n    \}/);
  assert.ok(match, 'refreshPrivateData não encontrada');
  assert.ok(match[0].includes('appState.allReadings'), 'allReadings não armazenado');
});

test('refreshPrivateData usa has() antes de set() para guardar só o mais recente por content_id (DESC → primeiro ganha)', () => {
  const match = script.match(/async function refreshPrivateData\(\)\s*\{[\s\S]*?\n    \}/);
  assert.ok(match[0].includes('appState.readings.has(entry.content_id)'), 'não checa duplicata — Map ainda pode guardar o mais antigo');
});

// ── pt-BR e es-AR: toda cópia nova tem os dois lados ───────────────────────

test('cada string nova do horóscopo diário aparece nos dois idiomas via IS_ARGENTINA (não só na tabela de tradução)', () => {
  const match = script.match(/function renderDailyHoroscope\(\)\s*\{[\s\S]*?\n    \}/)[0];
  const ternaries = match.match(/IS_ARGENTINA\s*\?/g) || [];
  assert.ok(ternaries.length >= 8, `esperava pelo menos 8 pares pt/es em renderDailyHoroscope, achou ${ternaries.length}`);
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
