// Testes da landing page do horóscopo grátis (portal-demo/horoscopo-gratis.html).
//
// Cobre: presença do formulário completo de nascimento nos dois idiomas, ausência
// de vazamento de idioma (PT em ES e vice-versa), diferença obrigatória de oferta
// entre pt-BR (Diário Astral via checkout) e es-AR (trial de 3 dias com cartão via
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

test('preço do Diário Astral vem de /api/catalog, não está hardcoded', () => {
  assert.match(html, /\/api\/catalog\?locale=/);
  assert.match(html, /price_label/);
  // Nenhum valor monetário fixo tipo "R$ 27,90" ou "ARS 27" deve aparecer no HTML.
  assert.ok(!/R\$\s?\d/.test(html), 'preço em reais hardcoded encontrado');
  assert.ok(!/ARS\s?\d/.test(html), 'preço em pesos hardcoded encontrado');
});

// ── Ambos os idiomas usam trial sem cartão (/api/trial/start) ───────────────

test('oferta pt-BR usa formulário de trial e chama /api/trial/start', () => {
  const match = html.match(/function renderDiarioAstralOffer\s*\(\)\s*\{([\s\S]*?)\n    \}/);
  assert.ok(match, 'função renderDiarioAstralOffer não encontrada');
  const body = match[1];
  assert.ok(body.includes('trial-form'), 'formulário de trial ausente no caminho pt-BR');
  assert.ok(body.includes('/api/trial/start') || html.includes('/api/trial/start'), 'chamada de trial ausente');
  assert.ok(body.includes('offerCta'), 'CTA do Diário Astral ausente no caminho pt-BR');
});

test('oferta es-AR usa formulário de trial e chama /api/trial/start', () => {
  const match = html.match(/function renderTrialOffer\s*\(\)\s*\{([\s\S]*?)\n    \}/);
  assert.ok(match, 'função renderTrialOffer não encontrada');
  const body = match[1];
  assert.ok(body.includes('trial-form'), 'formulário de trial ausente no caminho es-AR');
});

test('fluxo de trial es-AR chama POST /api/trial/start sem cartão no front e redireciona pro portal', () => {
  // Trial virou sem cartão em 08/08/2026 (api/app/subscriptions.py:start_trial):
  // só nome + e-mail, sem tokenizar cartão, sem preapproval no MP. O backend
  // devolve {status:"trialing"}, nunca init_point — não existe mais checkout
  // de cartão nesse fluxo, então o front não deve esperar um.
  assert.ok(!/sdk\.mercadopago\.com\/js\/v2/.test(html), 'SDK do Mercado Pago não deve mais ser carregado no front');
  assert.ok(!/new window\.MercadoPago\(/.test(html), 'Brick do Mercado Pago não deve mais ser montado no front');
  assert.match(html, /\/api\/trial\/start/);
  assert.match(html, /data\.status\s*!==\s*['"]trialing['"]/, 'front precisa validar status:"trialing" da resposta antes de redirecionar');
  assert.match(html, /window\.location\.href\s*=\s*PORTAL/, 'sucesso do trial deve redirecionar pro portal, não pra um checkout de cartão');
});

test('oferta es-AR só revela o e-mail depois do clique num CTA — <details> nativo, sem reload', () => {
  const match = html.match(/function renderTrialOffer\s*\(\)\s*\{([\s\S]*?)\n    \}/);
  assert.ok(match, 'função renderTrialOffer não encontrada');
  const body = match[1];
  const detailsIdx = body.indexOf('<details class="trial-reveal"');
  const summaryIdx = body.indexOf('<summary class="button secondary">');
  const formIdx = body.indexOf('<form id="trial-form">');
  assert.ok(detailsIdx >= 0, 'reveal <details> ausente na oferta es-AR');
  assert.ok(detailsIdx < summaryIdx && summaryIdx < formIdx, 'ordem errada: precisa ser details > summary(CTA) > form(dados)');
  // o resumo do <details> (visível fechado) usa startTrialCta; o CTA de dentro do form (só visível após abrir) usa mpCta.
  assert.match(body, /<summary class="button secondary">\$\{escapeHtml\(copy\.startTrialCta\)\}<\/summary>/);
  assert.match(body, /id="trial-submit">\$\{escapeHtml\(copy\.mpCta\)\}/);
});

// Mesmo motivo das 4 acima: trial não pede cartão nenhum desde 08/08/2026,
// então não existe mais "cartão digitado no Mercado Pago" pra afirmar.
test('copy es-AR deixa claro que não pede cartão pra ativar o trial', () => {
  assert.match(html, /Solo tu e-mail — sin tarjeta\./);
  assert.match(html, /No pedimos e-mail ni tarjeta para mostrarte tu horóscopo de hoy\./);
});

test('honesty es-AR cobre os 4 pontos reais do trial sem cartão: sem tarjeta, dia 4, não cobra nada, compra os 30 dias é voluntária', () => {
  const match = html.match(/honesty:\s*'([^']+)'/);
  assert.ok(match, 'chave honesty do es-AR não encontrada');
  const honesty = match[1];
  assert.match(honesty, /no hay tarjeta/i);
  assert.match(honesty, /no se cobra nada/i);
  assert.match(honesty, /[Dd]ía 4/);
  assert.match(honesty, /comprás 30 días por \{price\} cuando vos quieras/);
});

test('renderReading escolhe a oferta certa por idioma: es -> trial, pt -> Diário Astral', () => {
  assert.match(html, /const offerHtml = es \? renderTrialOffer\(\) : renderDiarioAstralOffer\(\);/);
});

// ── Copy honesta do trial: cancelamento explícito, sem cobrança nos 3 dias ──

// As 4 asserções abaixo eram do modelo antigo de trial COM cartão (cobrança
// automática no dia 4, salvo cancelamento). O trial virou sem cartão em
// 08/08/2026 (api/app/subscriptions.py:start_trial) — não existe mais cartão
// pra tokenizar, cobrança automática pra evitar, nem "Plan Luna" (produto
// se chama Diário Astral). Reescrito pra cobrir o que a copy promete hoje.

test('copy es-AR do trial deixa explícito que não fica nada salvo e não cobra sozinho, porque não pede cartão', () => {
  assert.match(html, /No pedimos tarjeta, así que no queda nada guardado ni se cobra solo/);
});

test('copy es-AR deixa explícito que o acesso grátis só termina no dia 4, sem cobrança automática', () => {
  assert.match(html, /Día 4: tu acceso gratis termina y listo — no se cobra nada, porque no hay tarjeta/);
});

test('copy pt-BR vende os 3 dias grátis, sem pegadinha', () => {
  const match = html.match(/\} : \{([\s\S]*?)\n\s*\};/);
  assert.ok(match, 'objeto de copy pt-BR não encontrado');
  const ptCopy = match[1];
  assert.ok(ptCopy.includes('3 dias grátis'), 'pt-BR não promete os 3 dias grátis');
  assert.ok(ptCopy.toLowerCase().includes('não pedimos cartão'), 'pt-BR não deixa explícito que não pede cartão');
  assert.ok(ptCopy.toLowerCase().includes('não fica nada salvo'), 'pt-BR não deixa explícito que nada fica salvo sem cartão');
});

test('copy pt-BR deixa explícito que o acesso grátis termina sem cobrar nada e que não há renovação automática', () => {
  assert.match(html, /[Ss]em renovação automática/);
  assert.match(html, /[Nn]ão cobramos nada|[Nn]ão se cobra nada|não fica nada salvo/i);
  const match = html.match(/function renderDiarioAstralOffer\s*\(\)\s*\{([\s\S]*?)\n    \}/);
  assert.ok(match, 'função renderDiarioAstralOffer não encontrada');
  assert.ok(match[1].includes('trial-form'), 'pt-BR deve usar CTA de trial (sem cartão)');
});

test('bullets do Diário Astral batem com o que o produto realmente entrega (horóscopo diário + guia do mês), sem prometer Mapa Astral de brinde', () => {
  const match = html.match(/\} : \{([\s\S]*?)\n\s*\};/);
  const ptCopy = match[1];
  assert.ok(/[Gg]uia do [Mm]ês/.test(ptCopy), 'bullet do Guia do Mês ausente no pt-BR');
  assert.ok(!/[Mm]apa [Aa]stral.*brinde/.test(ptCopy), 'pt-BR promete Mapa Astral de brinde, que o Diário Astral não entrega');
  const esMatch = html.match(/const copy = es \? \{([\s\S]*?)\n\s*\} : \{/);
  const esCopy = esMatch[1];
  assert.ok(/[Gg]uía del mes/.test(esCopy), 'bullet da Guía del mes ausente no es-AR');
});

test('preço do offer-price usa priceLabel vindo do /api/catalog, não valor fixo', () => {
  // priceLabel aparece no bloco price-anchor de ambas as funções de oferta
  assert.match(html, /escapeHtml\(priceLabel\)/);
  assert.match(html, /price-anchor/);
});

// ── Localização: nada de PT vazando pro ES e vice-versa ─────────────────────

test('bloco de copy es-AR não contém palavras em português', () => {
  const match = html.match(/const copy = es \? \{([\s\S]*?)\n\s*\} : \{/);
  assert.ok(match, 'objeto de copy es-AR não encontrado');
  const esCopy = match[1];
  for (const palavraPt of ['grátis', 'horóscopo grátis', 'não pedimos']) {
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

test('dispara StartTrial nos dois caminhos de trial (pt-BR e es-AR)', () => {
  assert.match(html, /fbq\('track', 'StartTrial'/);
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
