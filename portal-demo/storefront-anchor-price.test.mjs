// Trava do preço ancorado (de/por) na vitrine.
//
// A dona baixou os preços reais e o site passou a mostrar o preço antigo
// riscado ao lado do vigente. O riscado é SÓ exibição: quem cobra é
// api/app/pricing.py, e o guard de preço (api/tests/test_vitrine_checkout_price_guard.py)
// já trava os valores contra o backend.
//
// Este teste cuida da outra metade: que o riscado realmente apareça nos dois
// tipos de card (mapa avulso e combo), que ele NÃO apareça em produto sem
// âncora (Diário Astral), e que a classe usada tenha estilo — riscado sem CSS
// vira só um número duplicado ao lado do preço, que lê como erro.
//
// Roda com: ``node portal-demo/storefront-anchor-price.test.mjs``.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const storefront = readFileSync(join(here, 'storefront.html'), 'utf8');
const portalConfigAr = readFileSync(join(here, 'portal-config-ar.js'), 'utf8');

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

/** Recorta um trecho literal do storefront pelo prefixo da declaração. */
function slice(prefix, terminator = '};') {
  const start = storefront.indexOf(prefix);
  assert.notEqual(start, -1, `trecho não encontrado: ${prefix}`);
  const end = storefront.indexOf(terminator, start);
  assert.notEqual(end, -1, `fim do trecho não encontrado: ${prefix}`);
  return storefront.slice(start, end + terminator.length);
}

/**
 * Reconstrói o pedaço de render da vitrine (mapas de preço/âncora + cards) num
 * sandbox e devolve o HTML dos dois tipos de card. Assim o teste exercita o
 * template real do arquivo, não uma cópia que pode envelhecer sozinha.
 */
function renderCards(productId) {
  const pricesBR = slice('const pricesBR=', '};');
  const anchorsBR = slice('const anchorsBR=', '};');
  const anchorFor = slice("const anchorFor=id=>", ';');
  const anchorHtml = slice('const anchorHtml=id=>', '};');
  const mapCard = slice('const mapCard=p=>', '`;').replace(/;$/, '');
  const comboCard = slice('const comboCard=p=>', '`;').replace(/;$/, '');
  const source = `
    const es = false;
    const products = [];
    ${pricesBR}
    ${anchorsBR}
    const priceFor = id => pricesBR[id] || 'Consultar';
    ${anchorFor}
    ${anchorHtml}
    const kindLabel = { single: 'Leitura avulsa', combo: 'Combinação' };
    const ctaLabel = () => 'Ver leitura →';
    ${mapCard}
    ${comboCard}
    const p = { id: productId, kind: 'single', name: 'Produto', description: 'Descrição' };
    return { map: mapCard(p), combo: comboCard({ ...p, kind: 'combo' }) };
  `;
  return new Function('productId', source)(productId);
}

// ── Riscado presente onde a decisão comercial pediu ─────────────────────────

test('card de mapa avulso mostra o riscado antes do preço', () => {
  const { map } = renderCards('site:mapa_astral');
  assert.ok(map.includes('product-anchor'), 'card de mapa sem o riscado');
  assert.ok(map.includes('R$ 47,00'), 'âncora do mapa avulso ausente');
  assert.ok(map.includes('R$ 34,90'), 'preço vigente do mapa avulso ausente');
  assert.ok(
    map.indexOf('R$ 47,00') < map.indexOf('R$ 34,90'),
    'o riscado tem que vir ANTES do preço vigente',
  );
});

test('card de combo mostra o riscado antes do preço', () => {
  const { combo } = renderCards('site:combo_mapa_astral_amor');
  assert.ok(combo.includes('product-anchor'), 'card de combo sem o riscado');
  assert.ok(combo.includes('R$ 79,00'), 'âncora do combo ausente');
  assert.ok(combo.includes('R$ 58,90'), 'preço vigente do combo ausente');
  assert.ok(
    combo.indexOf('R$ 79,00') < combo.indexOf('R$ 58,90'),
    'o riscado tem que vir ANTES do preço vigente',
  );
});

// ── Sem riscado onde não existe âncora ──────────────────────────────────────

test('Diário Astral não ganha riscado — o preço dele não mudou', () => {
  const { map, combo } = renderCards('site:diario_astral');
  for (const html of [map, combo]) {
    assert.ok(!html.includes('product-anchor'), 'Diário Astral não pode mostrar riscado');
    assert.ok(html.includes('R$ 27,90'), 'preço do Diário Astral ausente');
  }
});

test('produto desconhecido não inventa riscado', () => {
  const { map } = renderCards('site:nao_existe');
  assert.ok(!map.includes('product-anchor'), 'produto sem preço não pode ter âncora');
});

// ── A classe do riscado precisa existir no CSS ──────────────────────────────

test('.product-anchor tem estilo próprio e é riscado de verdade', () => {
  const rule = storefront.match(/\.product-anchor\{([^}]*)\}/);
  assert.ok(rule, '.product-anchor usada no template mas sem regra CSS');
  assert.ok(
    rule[1].includes('line-through'),
    'âncora sem line-through lê como preço duplicado, não como desconto',
  );
});

// ── Mercado argentino ───────────────────────────────────────────────────────

test('catálogo AR expõe localizedAnchor para o mesmo conjunto de produtos', () => {
  assert.ok(portalConfigAr.includes('ARS_ANCHORS'), 'ARS_ANCHORS ausente no config AR');
  assert.ok(
    portalConfigAr.includes('localizedAnchor: ARS_ANCHORS[product.id] || null'),
    'produto AR não carrega localizedAnchor',
  );
  const anchorsBr = [...slice('const anchorsBR=', '};').matchAll(/'(site:[^']+)'\s*:/g)].map((m) => m[1]);
  const block = portalConfigAr.match(/const ARS_ANCHORS = Object\.freeze\(\{([\s\S]*?)\}\);/);
  assert.ok(block, 'bloco ARS_ANCHORS não encontrado');
  const anchorsAr = [...block[1].matchAll(/"(site:[^"]+)":/g)].map((m) => m[1]);
  assert.deepEqual(
    anchorsAr.slice().sort(),
    anchorsBr.slice().sort(),
    'BR e AR têm que riscar exatamente os mesmos produtos',
  );
});

test('a vitrine AR lê o riscado do catálogo, não de um literal solto', () => {
  assert.ok(
    storefront.includes('localizedAnchor'),
    'anchorFor precisa cair em localizedAnchor quando o locale é es-AR',
  );
});

// ── Checkout ────────────────────────────────────────────────────────────────

test('checkout risca a âncora do catálogo no produto e no total', () => {
  const checkout = readFileSync(join(here, 'checkout.html'), 'utf8');
  for (const id of ['product-anchor', 'total-anchor']) {
    assert.ok(checkout.includes(`id="${id}"`), `${id} ausente no resumo do pedido`);
  }
  assert.ok(
    checkout.includes("el.textContent = product.anchor_label || ''"),
    'checkout precisa ler anchor_label de /api/catalog',
  );
  assert.ok(
    checkout.includes('el.hidden = !product.anchor_label'),
    'sem anchor_label o riscado tem que ficar escondido, não vazio na tela',
  );
  const rule = checkout.match(/\.summary-anchor\{([^}]*)\}/);
  assert.ok(rule, '.summary-anchor sem regra CSS');
  assert.ok(rule[1].includes('line-through'), 'âncora do checkout sem line-through');
});

test('a âncora nunca vira o valor cobrado no checkout', () => {
  const checkout = readFileSync(join(here, 'checkout.html'), 'utf8');
  assert.ok(
    checkout.includes("text('total-price', product.price_label)"),
    'o total tem que continuar vindo de price_label',
  );
  assert.ok(
    !/amount(_minor)?\s*[:=]\s*[^;,)\n]*anchor/i.test(checkout),
    'anchor não pode alimentar nenhum campo de valor cobrado',
  );
});

// ── Runner ──────────────────────────────────────────────────────────────────

let failed = 0;
for (const [name, fn] of tests) {
  try {
    await fn();
    console.log(`✓ ${name}`);
  } catch (error) {
    failed += 1;
    console.error(`✗ ${name}\n  ${error.message}`);
  }
}

if (failed) {
  console.error(`storefront anchor price: ${tests.length - failed}/${tests.length} ok, ${failed} falhando`);
  process.exit(1);
}
console.log(`storefront anchor price: ${tests.length}/${tests.length} ok`);
