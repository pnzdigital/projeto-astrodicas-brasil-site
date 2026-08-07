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

test('formulário pede nome, data, hora (opcional) e cidade de nascimento', () => {
  assert.match(html, /name="name"/);
  assert.match(html, /name="birth_date"/);
  assert.match(html, /name="birth_time"/);
  assert.match(html, /name="birth_city"/);
});

test('campo de hora de nascimento não é required e é rotulado como opcional', () => {
  const fieldMatch = html.match(/<input id="f-time"[^>]*>/);
  assert.ok(fieldMatch, 'input f-time não encontrado');
  assert.ok(!fieldMatch[0].includes('required'), 'campo de hora ainda é required');
  assert.match(html, /Hora de nascimento \(opcional\)/);
});

test('chama o contrato POST /api/horoscopo/gratis com os campos certos, hora só quando preenchida', () => {
  assert.match(html, /\/api\/horoscopo\/gratis/);
  assert.match(html, /birth_date:\s*birthDate/);
  assert.match(html, /if \(birthTime\) payload\.birth_time = birthTime;/);
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

test('oferta pt-BR não tem formulário de trial nem chama /api/trial/start', () => {
  const match = html.match(/function renderPlanoLuaOffer\s*\(\)\s*\{([\s\S]*?)\n    \}/);
  assert.ok(match, 'função renderPlanoLuaOffer não encontrada');
  const body = match[1];
  assert.ok(!body.includes('trial-form'), 'formulário de trial vazou pro caminho pt-BR');
  assert.ok(!body.includes('/api/trial/start'), 'chamada de trial vazou pro caminho pt-BR');
  assert.ok(body.includes('offerCta'), 'CTA do Plano Lua ausente no caminho pt-BR');
});

test('oferta pt-BR aponta para o checkout existente do Plano Lua', () => {
  assert.match(html, /checkout\?product=site:plano_lua/);
});

test('oferta es-AR usa formulário de trial e chama /api/trial/start', () => {
  const match = html.match(/function renderTrialOffer\s*\(\)\s*\{([\s\S]*?)\n    \}/);
  assert.ok(match, 'função renderTrialOffer não encontrada');
  const body = match[1];
  assert.ok(body.includes('trial-form'), 'formulário de trial ausente no caminho es-AR');
});

test('fluxo de trial es-AR chama POST /api/trial/start sem cartão e redireciona pro init_point', () => {
  assert.ok(!/sdk\.mercadopago\.com\/js\/v2/.test(html), 'SDK do Mercado Pago não deve mais ser carregado no front');
  assert.ok(!/new window\.MercadoPago\(/.test(html), 'Brick do Mercado Pago não deve mais ser montado no front');
  assert.match(html, /\/api\/trial\/start/);
  assert.match(html, /data\.init_point/);
});

test('renderReading escolhe a oferta certa por idioma: es -> trial, pt -> Plano Lua', () => {
  assert.match(html, /const offerHtml = es \? renderTrialOffer\(\) : renderPlanoLuaOffer\(\);/);
});

// ── Copy honesta do trial: cancelamento explícito, sem cobrança nos 3 dias ──

test('copy es-AR do trial deixa explícito que dá pra cancelar sem pagar nada', () => {
  assert.match(html, /cancelás cuando quieras y no pagás nada/i);
});

test('copy es-AR deixa explícita a cobrança futura: dia 4 cobra o Plan Luna', () => {
  assert.match(html, /día 4.*se cobra el Plan Luna/i);
});

test('copy pt-BR vende os 3 dias grátis, sem pegadinha', () => {
  const match = html.match(/\} : \{([\s\S]*?)\n\s*\};/);
  assert.ok(match, 'objeto de copy pt-BR não encontrado');
  const ptCopy = match[1];
  assert.ok(ptCopy.includes('3 dias grátis'), 'pt-BR não promete os 3 dias grátis');
  assert.ok(ptCopy.toLowerCase().includes('cancela quando quiser'), 'pt-BR não deixa explícito o cancelamento');
  assert.ok(ptCopy.toLowerCase().includes('não paga nada'), 'pt-BR não deixa explícito que não cobra durante o teste');
});

test('copy pt-BR deixa explícito que só cobra depois dos 3 dias, e o CTA de trial ainda aponta pro checkout avulso', () => {
  assert.match(html, /[Dd]ia 4:.*cobramos \{price\}/);
  assert.match(html, /cancela quando quiser e para de cobrar no ciclo seguinte/i);
  assert.match(html, /CTA de trial pt-BR pendente de meio de pagamento/);
});

test('bullets do Plano Lua batem com o que o produto realmente entrega (horóscopo diário + guia do mês), sem prometer Mapa Astral de brinde', () => {
  const match = html.match(/\} : \{([\s\S]*?)\n\s*\};/);
  const ptCopy = match[1];
  assert.ok(/[Gg]uia do [Mm]ês/.test(ptCopy), 'bullet do Guia do Mês ausente no pt-BR');
  assert.ok(!/[Mm]apa [Aa]stral.*brinde/.test(ptCopy), 'pt-BR promete Mapa Astral de brinde, que o Plano Lua não entrega');
  const esMatch = html.match(/const copy = es \? \{([\s\S]*?)\n\s*\} : \{/);
  const esCopy = esMatch[1];
  assert.ok(/[Gg]uía del mes/.test(esCopy), 'bullet da Guía del mes ausente no es-AR');
});

test('preço do offer-price pt-BR usa priceLabel vindo do /api/catalog (via offerHonesty), não valor fixo', () => {
  assert.match(html, /offerHonesty\.replace\('\{price\}', priceLabel\)/);
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
