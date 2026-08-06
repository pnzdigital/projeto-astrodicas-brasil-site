// Testes da landing page do horóscopo grátis (portal-demo/horoscopo-gratis.html).
//
// Cobre: presença do formulário completo de nascimento nos dois idiomas, ausência
// de vazamento de idioma (PT em ES e vice-versa), diferença obrigatória de oferta
// entre pt-BR (Plano Lua via checkout) e es-AR (trial de 3 dias com cartão via
// Mercado Pago), disparo dos eventos do Meta Pixel e ausência de prova social falsa.
//
// Roda com: ``node portal-demo/horoscopo-gratis.test.mjs``.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, 'horoscopo-gratis.html'), 'utf8');

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

// ── Formulário completo de nascimento ───────────────────────────────────────

test('formulário pede nome, data, hora e cidade de nascimento', () => {
  assert.match(html, /name="name"/);
  assert.match(html, /name="birth_date"/);
  assert.match(html, /name="birth_time"/);
  assert.match(html, /name="birth_city"/);
});

test('chama o contrato POST /api/horoscopo/gratis com os campos certos', () => {
  assert.match(html, /\/api\/horoscopo\/gratis/);
  assert.match(html, /birth_date:\s*birthDate/);
  assert.match(html, /birth_time:\s*birthTime/);
  assert.match(html, /birth_city:\s*birthCity/);
  assert.match(html, /locale/);
});

test('renderiza title e body_html da resposta', () => {
  assert.match(html, /data\.title/);
  assert.match(html, /data\.body_html/);
});

// ── Preço vem da API, nunca hardcoded ───────────────────────────────────────

test('preço do Plano Lua vem de /api/catalog, não está hardcoded', () => {
  assert.match(html, /\/api\/catalog\?locale=/);
  assert.match(html, /price_label/);
  // Nenhum valor monetário fixo tipo "R$ 27,90" ou "ARS 27" deve aparecer no HTML.
  assert.ok(!/R\$\s?\d/.test(html), 'preço em reais hardcoded encontrado');
  assert.ok(!/ARS\s?\d/.test(html), 'preço em pesos hardcoded encontrado');
});

// ── Diferença obrigatória: trial com cartão só em es-AR ─────────────────────

test('oferta pt-BR não tem bloco de cartão nem chama /api/trial/start', () => {
  const match = html.match(/function renderPlanoLuaOffer\s*\(\)\s*\{([\s\S]*?)\n    \}/);
  assert.ok(match, 'função renderPlanoLuaOffer não encontrada');
  const body = match[1];
  assert.ok(!body.includes('card-brick'), 'bloco de cartão vazou pro caminho pt-BR');
  assert.ok(!body.includes('/api/trial/start'), 'chamada de trial vazou pro caminho pt-BR');
  assert.ok(body.includes('offerCta'), 'CTA do Plano Lua ausente no caminho pt-BR');
});

test('oferta pt-BR aponta para o checkout existente do Plano Lua', () => {
  assert.match(html, /checkout\?product=site:plano_lua/);
});

test('oferta es-AR usa o bloco de cartão do Mercado Pago e chama /api/trial/start', () => {
  const match = html.match(/function renderTrialOffer\s*\(\)\s*\{([\s\S]*?)\n    \}/);
  assert.ok(match, 'função renderTrialOffer não encontrada');
  const body = match[1];
  assert.ok(body.includes('card-brick'), 'bloco de cartão ausente no caminho es-AR');
});

test('fluxo de trial es-AR chama POST /api/trial/start com cartão via SDK Mercado Pago', () => {
  assert.match(html, /sdk\.mercadopago\.com\/js\/v2/);
  assert.match(html, /new window\.MercadoPago\(/);
  assert.match(html, /\/api\/trial\/start/);
  assert.match(html, /\.\.\.formData/);
});

test('renderReading escolhe a oferta certa por idioma: es -> trial, pt -> Plano Lua', () => {
  assert.match(html, /const offerHtml = es \? renderTrialOffer\(\) : renderPlanoLuaOffer\(\);/);
});

// ── Copy honesta do trial: cancelamento explícito, sem cobrança nos 3 dias ──

test('copy es-AR do trial deixa explícito que dá pra cancelar sem pagar nada', () => {
  assert.match(html, /cancelás cuando quieras antes de que terminen y no pagás nada/i);
});

// ── Localização: nada de PT vazando pro ES e vice-versa ─────────────────────

test('bloco de copy es-AR não contém palavras em português', () => {
  const match = html.match(/const copy = es \? \{([\s\S]*?)\n\s*\} : \{/);
  assert.ok(match, 'objeto de copy es-AR não encontrado');
  const esCopy = match[1];
  for (const palavraPt of ['grátis', 'horóscopo grátis', 'não pedimos', 'Plano Lua']) {
    assert.ok(!esCopy.includes(palavraPt), `português vazou no bloco es-AR: "${palavraPt}"`);
  }
});

test('bloco de copy pt-BR não contém palavras em espanhol', () => {
  const match = html.match(/\} : \{([\s\S]*?)\n\s*\};/);
  assert.ok(match, 'objeto de copy pt-BR não encontrado');
  const ptCopy = match[1];
  for (const palavraEs of ['gratis · al instante', 'Empezar', 'Plan Luna', 'cancelás']) {
    assert.ok(!ptCopy.includes(palavraEs), `espanhol vazou no bloco pt-BR: "${palavraEs}"`);
  }
});

test('seletor de idioma aponta pro par correto de rotas', () => {
  assert.match(html, /href="\/horoscopo-gratis" data-lang="pt"/);
  assert.match(html, /href="\/es\/horoscopo-gratis" data-lang="es"/);
});

// ── Meta Pixel: ViewContent, Lead, StartTrial/InitiateCheckout ─────────────

test('dispara ViewContent na chegada', () => {
  assert.match(html, /fbq\('track', 'ViewContent'/);
});

test('dispara Lead ao gerar o horóscopo grátis', () => {
  assert.match(html, /fbq\('track', 'Lead'/);
});

test('dispara StartTrial no caminho es-AR e InitiateCheckout no caminho pt-BR', () => {
  assert.match(html, /fbq\('track', 'StartTrial'/);
  assert.match(html, /fbq\('track', 'InitiateCheckout'/);
});

test('inclui o script do meta-pixel', () => {
  assert.match(html, /<script src="\/meta-pixel\.js">/);
});

// ── Nada de prova social falsa, depoimento fake ou contador mentiroso ───────

test('não introduz depoimentos, contadores fake ou avaliação inventada', () => {
  assert.ok(!/\+?1\.?469/.test(html), 'contador de mapas calculados voltou');
  assert.ok(!/★★★★★/.test(html), 'estrelas de avaliação inventadas');
  assert.ok(!/id="relatos"/.test(html), 'seção de depoimentos encenados');
  assert.ok(!html.includes('class="chats"'), 'carcaça de chat fake encontrada');
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
