// Trava contra qualquer oferta de portal-demo/index.html anunciar produto
// que o plano comprado não libera.
//
// Direitos são derivados AO VIVO de api/app/pricing.py (PRODUCT_TITLES +
// BUNDLES) via subprocesso Python — a mesma fonte que api/app/checkout.py
// consulta para conceder entitlements. Isso evita a armadilha de uma lista
// escrita à mão aqui divergir do pacote real: se pricing.py mudar (novo
// bundle, título renomeado), este teste acompanha sem precisar de edição.
//
// Roda com: ``node portal-demo/plan-copy-no-oversell.test.mjs``.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { execFileSync } from 'node:child_process';

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, '..');
const html = readFileSync(join(here, 'index.html'), 'utf8');

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

function extractOfferLine(source, key) {
  const marker = `'${key}': [`;
  const start = source.indexOf(marker);
  assert.ok(start !== -1, `chave de oferta não encontrada: ${key}`);
  const end = source.indexOf('],', start);
  return source.slice(start, end);
}

// Fonte única de verdade: importa pricing.py de verdade (não regex sobre o
// texto) e devolve PRODUCT_TITLES + BUNDLES como JSON.
function loadPricingCatalog() {
  const script = [
    'import json, sys',
    "sys.path.insert(0, 'api')",
    'from app import pricing',
    'print(json.dumps({',
    '  "titles": pricing.PRODUCT_TITLES,',
    '  "bundles": pricing.BUNDLES,',
    '  "products": list(pricing.PRICES_BRL_MINOR.keys()),',
    '}))',
  ].join('\n');
  const out = execFileSync('python3', ['-c', script], { cwd: repoRoot, encoding: 'utf8' });
  return JSON.parse(out);
}

const CATALOG = loadPricingCatalog();

// Tudo que uma compra de `productId` efetivamente libera: ela mesma + bundle.
function entitledProducts(productId) {
  const granted = new Set([productId]);
  for (const item of CATALOG.bundles[productId] || []) granted.add(item);
  return granted;
}

const LOCALES = ['pt-BR', 'es-AR'];

for (const productId of CATALOG.products) {
  const granted = entitledProducts(productId);
  const notGranted = CATALOG.products.filter((id) => !granted.has(id));

  test(`oferta ${productId} não promete produto que o pacote não libera`, () => {
    if (!html.includes(`'${productId}': [`)) return; // produto sem card no portal (ex.: bumps)
    const line = extractOfferLine(html, productId);
    for (const otherId of notGranted) {
      for (const locale of LOCALES) {
        const title = CATALOG.titles[otherId]?.[locale];
        if (!title) continue;
        assert.ok(
          !line.includes(title),
          `oferta ${productId} menciona "${title}" (${otherId}/${locale}), mas pricing.BUNDLES não libera esse item nesse plano`,
        );
      }
    }
  });
}

test('calendário lunar não promete "do ano" — conteúdo real é do ciclo/mês corrente', () => {
  assert.ok(
    !/expansão do ano|fases do ano|expansión del año|fases del año/i.test(html),
    'copy do calendário lunar promete cobertura anual que o produto não entrega',
  );
});

let pass = 0;
for (const [name, fn] of tests) {
  try {
    fn();
    pass++;
    console.log(`# ok - ${name}`);
  } catch (err) {
    console.log(`# FAIL - ${name}`);
    console.error(err);
    process.exitCode = 1;
  }
}
console.log(`# plan-copy-no-oversell: ${pass}/${tests.length} ok`);
