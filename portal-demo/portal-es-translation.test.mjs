/**
 * Verifica que strings visíveis ao usuário em português têm par em espanhol
 * para o portal /es. A lógica de tradução usa dois mecanismos:
 *   1. Ternário IS_ARGENTINA na mesma linha do JS (para strings dinâmicas).
 *   2. Chave no objeto `replacements` do applySpanishPortalCopy (para HTML estático).
 *
 * Exceções aceitas:
 *   - Comentários de código (// ...)
 *   - Nomes de produto: "Diário Astral", "Diario Astral", "AstroDicas"
 *   - Chaves do próprio dicionário (o lado PT da entrada)
 *   - Strings dentro de Object.assign/replacements (são as chaves, não conteúdo)
 */

import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { strict as assert } from 'node:assert';

const src = readFileSync(new URL('./index.html', import.meta.url), 'utf8');

// Extrai todos os valores das chaves do dicionário `replacements` (lado PT).
function extractDictKeys() {
  const keys = new Set();
  // Captura strings dentro das atribuições do replacements/Object.assign
  const blockRe = /const replacements\s*=\s*\{([\s\S]*?)\};\s*Object\.assign/;
  const assignRe = /Object\.assign\(replacements,\s*\{([\s\S]*?)\}\s*\);/g;
  const entryRe = /'((?:[^'\\]|\\.)*)'\s*:/g;

  const blockMatch = src.match(blockRe);
  if (blockMatch) {
    let m;
    while ((m = entryRe.exec(blockMatch[1])) !== null) keys.add(m[1]);
  }
  let am;
  while ((am = assignRe.exec(src)) !== null) {
    entryRe.lastIndex = 0;
    let m;
    while ((m = entryRe.exec(am[1])) !== null) keys.add(m[1]);
  }
  return keys;
}

// Verifica que data-toast values com marcadores PT têm par de tradução.
test('data-toast: todos os valores com padrão PT têm tradução para /es', () => {
  const PT = /[çã]|você|seu\b|sua\b|não\b|amanhã|hoje\b|salvo|gerado/i;

  // Valores estáticos de data-toast no HTML
  const toastValues = [...src.matchAll(/data-toast="([^"]+)"/g)].map((m) => m[1]);

  // Pares tratados no loop de data-toast (node.dataset.toast.replace(FROM, TO))
  const handledFrom = new Set(
    [...src.matchAll(/node\.dataset\.toast\s*=\s*node\.dataset\.toast\.replace\('([^']+)'/g)].map((m) => m[1])
  );

  const errors = [];
  for (const val of toastValues) {
    if (!PT.test(val)) continue;
    if (handledFrom.has(val)) continue;
    errors.push(val);
  }
  assert.deepEqual(errors, [], `data-toast sem tradução ES:\n${errors.map((e) => `  "${e}"`).join('\n')}`);
});

// Verifica strings estáticas do HTML com padrões PT que não têm entrada no dict.
test('strings estáticas no HTML têm entrada no dicionário de replacements', () => {
  const dictKeys = extractDictKeys();

  // Padrões que indicam português visível ao usuário (não em comentários, não em JS lógica)
  // Foca em sequências de palavras PT que podem aparecer como texto de nó.
  const KNOWN_PT_PHRASES = [
    'Área segura do seu céu · canal web',
    'Plano e acessos',
    'Conta AstroDicas',
    'Data de nascimento',
    'Cidade de nascimento',
    'Fuso horário',
    'Parceiro (opcional)',
    'Salvar dados →',
    'Adicionar AstroDicas à tela de início',
    'Esses dados ficam associados à sua conta web e são usados para gerar suas leituras. Os campos do parceiro são opcionais.',
  ];

  const missing = KNOWN_PT_PHRASES.filter((phrase) => !dictKeys.has(phrase));
  assert.deepEqual(missing, [], `Frases PT sem par no dict:\n${missing.map((p) => `  "${p}"`).join('\n')}`);
});

// Verifica que showToast chamadas sem IS_ARGENTINA não carregam strings em PT.
test('showToast em JS usa IS_ARGENTINA para strings com padrão PT', () => {
  // Procura showToast('...') sem IS_ARGENTINA na mesma linha — seria uma chamada PT hardcoded.
  const PT_TOAST = /showToast\('([^']*(?:ção|você|seu |sua |não\b|amanhã|Complete seus|Seu acesso está)[^']*)'\)/g;
  const errors = [];
  let m;
  while ((m = PT_TOAST.exec(src)) !== null) {
    // Verifica que IS_ARGENTINA aparece na mesma linha lógica
    const lineStart = src.lastIndexOf('\n', m.index) + 1;
    const lineEnd = src.indexOf('\n', m.index + m[0].length);
    const line = src.slice(lineStart, lineEnd === -1 ? undefined : lineEnd);
    if (!line.includes('IS_ARGENTINA')) {
      errors.push(m[1]);
    }
  }
  assert.deepEqual(errors, [], `showToast PT sem IS_ARGENTINA:\n${errors.map((e) => `  "${e}"`).join('\n')}`);
});
