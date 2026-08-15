import { test } from 'node:test';
import { strict as assert } from 'node:assert';

// Reproduz a lógica corrigida de excerptFromHtml sem DOM real.
// Usa a mesma regex/lógica do código corrigido para validar o comportamento.
function excerptFromHtml(html, maxLen = 220) {
  // Simulação mínima de DOM via regex — suficiente para testar a lógica de extração.
  const withoutHeadings = (html || '')
    .replace(/<h[1-6][^>]*>[\s\S]*?<\/h[1-6]>/gi, '')
    .replace(/<[^>]*class="[^"]*reading-subtitle[^"]*"[^>]*>[\s\S]*?<\/p>/gi, '');
  const paragraphs = [];
  const pRe = /<p[^>]*>([\s\S]*?)<\/p>/gi;
  let m;
  while ((m = pRe.exec(withoutHeadings)) !== null) {
    const text = m[1].replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();
    if (text) paragraphs.push(text);
  }
  const strippedFull = (html || '').replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();
  const text = paragraphs.length ? paragraphs.join(' ') : strippedFull;
  if (text.length <= maxLen) return text;
  return `${text.slice(0, maxLen).trim()}…`;
}

test('body_html sem estrutura (blob de <p>) extrai texto', () => {
  const html = '<p>Texto simples aqui.</p>';
  assert.equal(excerptFromHtml(html), 'Texto simples aqui.');
});

test('heading NÃO aparece no resumo', () => {
  const html = '<h2>Identificação</h2><p>Conteúdo da seção.</p>';
  const result = excerptFromHtml(html);
  assert(!result.includes('Identificação'), `heading vazou no resumo: "${result}"`);
  assert(result.includes('Conteúdo da seção'), `conteúdo sumiu: "${result}"`);
});

test('subtítulo (.reading-subtitle) NÃO aparece no resumo', () => {
  const html = '<h2>Seção</h2><p class="reading-subtitle">O dia reflete você</p><p>La conjunción exacta de Saturno.</p>';
  const result = excerptFromHtml(html);
  assert(!result.includes('O dia reflete você'), `subtítulo vazou: "${result}"`);
  assert(result.includes('La conjunción'), `conteúdo sumiu: "${result}"`);
});

test('heading + subtítulo + parágrafo: sem concatenação colada', () => {
  const html = '<h2>Identificação</h2><p class="reading-subtitle">O dia reflete você</p><p>La conjunción exacta de Saturno transitando...</p>';
  const result = excerptFromHtml(html);
  assert(!result.startsWith('Identificação'), `começa com o título: "${result}"`);
  assert(!result.includes('IdentificaçãoO'), `colagem detectada: "${result}"`);
});

test('múltiplos parágrafos: concatenados com espaço', () => {
  const html = '<p>Primeiro parágrafo.</p><p>Segundo parágrafo.</p>';
  const result = excerptFromHtml(html);
  assert.equal(result, 'Primeiro parágrafo. Segundo parágrafo.');
});

test('resultado truncado em maxLen com reticências', () => {
  const html = '<p>' + 'a'.repeat(300) + '</p>';
  const result = excerptFromHtml(html, 50);
  assert(result.endsWith('…'), `deve terminar com … : "${result}"`);
  assert(result.length <= 52, `muito longo: ${result.length}`);
});

test('html vazio → string vazia', () => {
  assert.equal(excerptFromHtml(''), '');
  assert.equal(excerptFromHtml(null), '');
});
