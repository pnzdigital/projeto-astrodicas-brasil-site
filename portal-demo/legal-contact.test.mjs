// Garante que as páginas legais e de suporte tenham identidade e contato
// reais em vez dos placeholders [[PREENCHER]] / [[COMPLETAR]], e que o
// e-mail oficial aponte sempre para comercial@pnzdigital.com.br.
//
// O dono decidiu que CNPJ, razão social completa e endereço NÃO vão ao ar,
// então este teste também falharia se esses dados fossem inventados aqui.
//
// Roda com: ``node portal-demo/legal-contact.test.mjs``.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const termos = readFileSync(join(here, 'termos.html'), 'utf8');
const privacidade = readFileSync(join(here, 'privacidade.html'), 'utf8');
const suporte = readFileSync(join(here, 'suporte.html'), 'utf8');

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

// ── Identidade nas três páginas ─────────────────────────────────────────────

test('termos.html não tem placeholder de identidade/contato', () => {
  assert.ok(!/\[\[PREENCHER/.test(termos), '[[PREENCHER]] sobrou em termos.html');
  assert.ok(!/\[\[COMPLETAR/.test(termos), '[[COMPLETAR]] (es-AR) sobrou em termos.html');
});

test('privacidade.html não tem placeholder de identidade/contato', () => {
  assert.ok(!/\[\[PREENCHER/.test(privacidade), '[[PREENCHER]] sobrou em privacidade.html');
  assert.ok(!/\[\[COMPLETAR/.test(privacidade), '[[COMPLETAR]] (es-AR) sobrou em privacidade.html');
});

test('suporte.html não tem placeholder de identidade/contato', () => {
  assert.ok(!/\[\[PREENCHER/.test(suporte), '[[PREENCHER]] sobrou em suporte.html');
  assert.ok(!/\[\[COMPLETAR/.test(suporte), '[[COMPLETAR]] (es-AR) sobrou em suporte.html');
});

test('as três páginas declaram o operador do site (PNZ Digital)', () => {
  assert.ok(/PNZ Digital/.test(termos), 'operador ausente em termos.html');
  assert.ok(/PNZ Digital/.test(privacidade), 'operador ausente em privacidade.html');
  assert.ok(/PNZ Digital/.test(suporte), 'operador ausente em suporte.html');
});

test('as três páginas citam o e-mail oficial', () => {
  for (const [nome, html] of [['termos', termos], ['privacidade', privacidade], ['suporte', suporte]]) {
    assert.ok(
      html.includes('comercial@pnzdigital.com.br'),
      `comercial@pnzdigital.com.br ausente em ${nome}.html`,
    );
  }
});

test('nenhum mailto ficou vazio nas três páginas', () => {
  for (const [nome, html] of [['termos', termos], ['privacidade', privacidade], ['suporte', suporte]]) {
    const mailtos = html.match(/mailto:[^\s"'<>]*/g) ?? [];
    for (const m of mailtos) {
      assert.notStrictEqual(
        m, 'mailto:', `${nome}.html tem mailto vazio: ${m}`,
      );
      assert.ok(
        !/\[\[PREENCHER/.test(m) && !/\[\[COMPLETAR/.test(m),
        `${nome}.html tem mailto com placeholder: ${m}`,
      );
    }
  }
});

test('CNPJ, razão social completa e endereço NÃO aparecem (decisão do dono)', () => {
  // Garante que ninguém inventou esses dados nas páginas. Procuramos os
  // rótulos — se voltarem mesmo com conteúdo válido, o teste grita.
  for (const [nome, html] of [['termos', termos], ['privacidade', privacidade]]) {
    assert.ok(!/CNPJ/i.test(html), `${nome}.html inventou um CNPJ`);
    assert.ok(!/razão social/i.test(html), `${nome}.html cita "razão social" — caiu o guarda`);
    assert.ok(!/sede em/i.test(html), `${nome}.html cita endereço (sede em ...) — caiu o guarda`);
    assert.ok(!/inscrita no/i.test(html), `${nome}.html cita inscrição — caiu o guarda`);
  }
});

// ── Es-AR: se existe, precisa estar coerente ────────────────────────────────

test('versão es-AR presente e também cita o e-mail oficial', () => {
  // O bloco es-AR mora dentro do mesmo HTML (script de i18n).
  for (const [nome, html] of [['termos', termos], ['privacidade', privacidade], ['suporte', suporte]]) {
    assert.ok(/es-AR|spanish|español|Espa[ñn]ol/i.test(html), `${nome}.html não tem versão es-AR`);
    assert.ok(
      html.includes('comercial@pnzdigital.com.br'),
      `${nome}.html: e-mail oficial ausente — verifique se o bloco es-AR usa outro endereço`,
    );
  }
});

// ── Suporte: o JS que preenche os IDs tem que usar o e-mail correto ─────────

test('suporte.html: JS que monta o bloco es-AR usa o e-mail oficial', () => {
  // O JS faz innerHTML com a string do e-mail e do mailto. Se ele ainda
  // tiver um placeholder, o usuário final verá placeholder no ar quando o
  // idioma for es-AR — o teste pega antes.
  assert.ok(
    suporte.includes("comercial@pnzdigital.com.br"),
    'suporte.html não cita o e-mail oficial — nem no JS que monta o bloco es-AR',
  );
  assert.ok(
    !/mailto:\s*\[\[/.test(suporte) && !/mailto:\s*['"]?\s*\+/.test(suporte),
    'suporte.html ainda monta mailto com placeholder',
  );
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
  console.error(`legal-contact: ${tests.length - failed}/${tests.length} ok, ${failed} falhando`);
  process.exit(1);
}
console.log(`legal-contact: ${tests.length}/${tests.length} ok`);
