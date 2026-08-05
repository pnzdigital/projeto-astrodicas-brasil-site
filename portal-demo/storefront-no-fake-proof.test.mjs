// Trava contra prova social falsa na storefront.
//
// O site já exibiu depoimentos encenados (personagens inventados em formato de
// conversa de WhatsApp, com fotos de stock como avatares) e uma estatística de
// mapas calculados que nenhum dado do produto sustenta. Isso foi removido.
//
// Este teste existe para que a remoção não seja desfeita por acidente: qualquer
// depoimento, nome de cliente, avatar ou métrica não verificável que volte para
// storefront.html derruba a suíte.
//
// A regra é simples: o site só afirma o que consegue sustentar. Se um dia
// existirem depoimentos REAIS e autorizados, este teste precisa ser reescrito
// de propósito — junto com a prova de que os depoimentos são reais.
//
// Roda com: ``node portal-demo/storefront-no-fake-proof.test.mjs``.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const storefront = readFileSync(join(here, 'storefront.html'), 'utf8');

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

// ── Estruturas que sustentavam os depoimentos ───────────────────────────────

test('a seção de relatos não existe mais', () => {
  assert.ok(
    !/id="relatos"/.test(storefront),
    'seção #relatos voltou: era o bloco de depoimentos encenados',
  );
});

test('a carcaça de conversa de WhatsApp não existe mais', () => {
  for (const marker of ['class="chats"', 'id="chats"', 'class="wa"', 'class="wa-head"', 'class="wa-body"', 'class="bubble"']) {
    assert.ok(
      !storefront.includes(marker),
      `marcador de chat fake encontrado: ${marker}`,
    );
  }
});

test('o CSS do chat fake não ficou órfão', () => {
  for (const rule of ['.chats{', '.wa{', '.wa-head', '.wa-body', '.bubble', '.wa-note', '.phone-status']) {
    assert.ok(
      !storefront.includes(rule),
      `CSS órfão do bloco de depoimentos: ${rule}`,
    );
  }
});

test('não há JS montando conversas', () => {
  assert.ok(
    !/const chats\s*=/.test(storefront),
    'array de conversas encenadas voltou ao script',
  );
  assert.ok(
    !/getElementById\('chats'\)/.test(storefront),
    'render das conversas encenadas voltou ao script',
  );
});

// ── Conteúdo: nomes, avisos e métricas ──────────────────────────────────────

test('nenhum nome de cliente inventado aparece', () => {
  // Personagens que o site exibia como se fossem clientes reais.
  const personagens = [
    'Camila R.', 'Juliana M.', 'Fernanda C.', 'Patrícia L.', 'Rita S.', 'Aline P.',
    'Sofía M.', 'Valentina G.', 'Julieta C.',
  ];
  for (const nome of personagens) {
    assert.ok(
      !storefront.includes(nome),
      `nome de cliente encenado voltou: ${nome}`,
    );
  }
});

test('o aviso de avatar editorial não existe (nem o seu objeto)', () => {
  // O aviso admitia a encenação. Some junto com os depoimentos: sem depoimento,
  // o aviso não tem objeto. Se ele voltar sozinho, é sinal de que os
  // depoimentos voltaram também.
  for (const trecho of ['editoriais', 'editoriales', 'Avatares ilustrativos', 'Avatares ilustrativos.', 'fotos autorizadas', 'fotos autorizados']) {
    assert.ok(
      !storefront.includes(trecho),
      `aviso de encenação voltou: ${trecho}`,
    );
  }
});

test('as chaves de copy dos depoimentos sumiram nos dois idiomas', () => {
  for (const chave of ['storiesTag', 'storiesTitle', 'storiesText', 'storiesNote', 'navStories']) {
    assert.ok(
      !storefront.includes(chave),
      `chave de copy dos depoimentos voltou: ${chave}`,
    );
  }
});

test('nenhuma foto de avatar é carregada', () => {
  assert.ok(
    !storefront.includes('assets/testimonials'),
    'foto de stock usada como avatar de cliente voltou',
  );
});

test('não há estatística de volume nem nota média', () => {
  // "+1.469 mapas calculados" e "4,8 ★" não têm origem verificável no produto.
  assert.ok(!/\+?1\.?469/.test(storefront), 'estatística de mapas calculados voltou');
  assert.ok(!/mapas calculados/.test(storefront), 'contagem de mapas calculados voltou');
  assert.ok(!/cartas calculadas/.test(storefront), 'contagem de cartas calculadas voltou');
  assert.ok(!/4,8\s*★/.test(storefront), 'nota média inventada voltou');
  assert.ok(!/★★★★★/.test(storefront), 'estrelas de avaliação voltaram');
});

// ── O que entrou no lugar precisa continuar lá ──────────────────────────────

test('a seção honesta existe nos dois idiomas', () => {
  assert.ok(
    storefront.includes('id="garantias"'),
    'a seção de fatos verificáveis sumiu — o buraco do layout voltou',
  );
  for (const chave of ['assuranceTag', 'assuranceTitle', 'assuranceText', 'navAssurance']) {
    assert.ok(
      storefront.includes(chave),
      `chave de copy da seção honesta ausente: ${chave}`,
    );
  }
  // A copy es-AR mora no objeto de tradução; se a chave aparece só uma vez,
  // um dos idiomas ficou para trás.
  const ocorrencias = storefront.split('assuranceTitle').length - 1;
  assert.ok(
    ocorrencias >= 2,
    `assuranceTitle aparece ${ocorrencias}x: falta a versão es-AR`,
  );
});

test('a seção honesta só afirma o que o site sustenta', () => {
  // Cada afirmação da seção nova tem origem no próprio código/copy:
  // prazo de arrependimento (termos.html), cancelamento (FAQ), prévia grátis
  // (rota /api/preview/natal) e o que a leitura inclui (upsellItems).
  assert.ok(storefront.includes('7 dias'), 'prazo de arrependimento pt-BR ausente');
  assert.ok(storefront.includes('7 días'), 'prazo de arrependimento es-AR ausente');
});

// ── Runner ──────────────────────────────────────────────────────────────────

let failed = 0;
for (const [name, fn] of tests) {
  try {
    await fn();
  } catch (error) {
    failed += 1;
    console.error(`✗ ${name}\n  ${error.message}`);
  }
}

if (failed) {
  console.error(`storefront no-fake-proof: ${tests.length - failed}/${tests.length} ok, ${failed} falhando`);
  process.exit(1);
}
console.log(`storefront no-fake-proof: ${tests.length}/${tests.length} ok`);
