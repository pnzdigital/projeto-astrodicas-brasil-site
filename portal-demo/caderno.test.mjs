// Garante que o "Caderno do céu" da vitrine linka para artigos reais que
// existem em disco, com SEO básico (title/canonical/hreflang) e CTA válido.
//
// Roda com: ``node portal-demo/caderno.test.mjs``.

import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { parseHTML } from 'linkedom';

const here = dirname(fileURLToPath(import.meta.url));
const storefrontHtml = readFileSync(join(here, 'storefront.html'), 'utf8');

const articles = [
  'caderno/ascendente-hora-nascimento.html',
  'caderno/sinastria-nao-e-sentenca.html',
  'caderno/o-que-observar-retrogrado.html',
];

const routes = [
  ['/caderno/ascendente', 'caderno/ascendente-hora-nascimento.html'],
  ['/es/caderno/ascendente', 'caderno/ascendente-hora-nascimento.html'],
  ['/caderno/sinastria', 'caderno/sinastria-nao-e-sentenca.html'],
  ['/es/caderno/sinastria', 'caderno/sinastria-nao-e-sentenca.html'],
  ['/caderno/retrogrado', 'caderno/o-que-observar-retrogrado.html'],
  ['/es/caderno/retrogrado', 'caderno/o-que-observar-retrogrado.html'],
];

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test('cada artigo do Caderno existe em disco', () => {
  for (const rel of articles) {
    assert.ok(existsSync(join(here, rel)), `arquivo ausente: ${rel}`);
  }
});

test('cada artigo carrega e tem title/canonical/hreflang PT+ES', () => {
  for (const rel of articles) {
    const html = readFileSync(join(here, rel), 'utf8');
    const { document } = parseHTML(html);
    const title = document.getElementById('meta-title');
    const canonical = document.getElementById('meta-canonical');
    assert.ok(title && title.textContent.trim().length > 0, `${rel} sem <title>`);
    assert.ok(canonical && /^https:\/\/astrodicas\.pnzdigital\.com\.br\//.test(canonical.getAttribute('href')), `${rel} sem canonical válido`);
    const hreflangs = [...document.querySelectorAll('link[rel="alternate"][hreflang]')].map(l => l.getAttribute('hreflang'));
    assert.ok(hreflangs.includes('pt-BR'), `${rel} sem hreflang pt-BR`);
    assert.ok(hreflangs.includes('es-AR'), `${rel} sem hreflang es-AR`);
    assert.ok(hreflangs.includes('x-default'), `${rel} sem hreflang x-default`);
  }
});

test('cada artigo tem JSON-LD Article válido', () => {
  for (const rel of articles) {
    const html = readFileSync(join(here, rel), 'utf8');
    const { document } = parseHTML(html);
    const script = document.getElementById('jsonld');
    assert.ok(script, `${rel} sem script#jsonld`);
    const data = JSON.parse(script.textContent);
    assert.strictEqual(data['@type'], 'Article', `${rel} JSON-LD não é Article`);
    assert.ok(data.headline && data.headline.length > 0, `${rel} JSON-LD sem headline`);
  }
});

test('cada artigo tem bloco JS que traduz para ES ao entrar por /es/', () => {
  for (const rel of articles) {
    const html = readFileSync(join(here, rel), 'utf8');
    assert.ok(html.includes("location.pathname.startsWith('/es/')"), `${rel} sem lógica de detecção /es/`);
    assert.ok(html.includes('es-AR'), `${rel} não seta lang es-AR`);
  }
});

test('cada artigo termina com CTA para /horoscopo ou para os produtos (#leituras)', () => {
  for (const rel of articles) {
    const html = readFileSync(join(here, rel), 'utf8');
    const { document } = parseHTML(html);
    const primary = document.getElementById('cta-primary');
    const secondary = document.getElementById('cta-secondary');
    assert.ok(primary, `${rel} sem cta-primary`);
    assert.ok(secondary, `${rel} sem cta-secondary`);
    assert.ok(
      primary.getAttribute('href') === '/horoscopo',
      `${rel} cta-primary não aponta para /horoscopo`,
    );
    assert.ok(
      secondary.getAttribute('href') === '/#leituras',
      `${rel} cta-secondary não aponta para #leituras`,
    );
  }
});

test('cada artigo tem 700 a 1100 palavras no corpo (PT)', () => {
  for (const rel of articles) {
    const html = readFileSync(join(here, rel), 'utf8');
    const { document } = parseHTML(html);
    const main = document.getElementById('conteudo');
    const words = main.textContent.trim().split(/\s+/).filter(Boolean);
    assert.ok(
      words.length >= 700 && words.length <= 1100,
      `${rel} tem ${words.length} palavras, fora de 700-1100`,
    );
  }
});

test('storefront.html: cada card do Caderno do céu linka para um artigo que existe (PT)', () => {
  const { document } = parseHTML(storefrontHtml);
  const script = [...document.querySelectorAll('script')].find(s => s.textContent.includes('const notes='));
  assert.ok(script, 'storefront.html sem bloco const notes=');
  const src = script.textContent;
  const ptMatch = src.match(/\]:\[([\s\S]*?)\];\s*document\.getElementById\('journal'\)/);
  assert.ok(ptMatch, 'não encontrou array PT de notes');
  const hrefs = [...ptMatch[1].matchAll(/'(\/caderno\/[a-z-]+)'/g)].map(m => m[1]);
  assert.strictEqual(hrefs.length, 3, `esperava 3 links de card PT, achou ${hrefs.length}`);
  const routeMap = new Map(routes);
  for (const href of hrefs) {
    assert.ok(routeMap.has(href), `card linka para rota desconhecida: ${href}`);
    const target = routeMap.get(href);
    assert.ok(existsSync(join(here, target)), `rota ${href} aponta para arquivo inexistente: ${target}`);
  }
});

test('storefront.html: cards ES linkam para /es/caderno/* existentes', () => {
  const { document } = parseHTML(storefrontHtml);
  const script = [...document.querySelectorAll('script')].find(s => s.textContent.includes('const notes='));
  const src = script.textContent;
  const esMatch = src.match(/const notes=es\?\[([\s\S]*?)\]:\[/);
  assert.ok(esMatch, 'não encontrou array ES de notes');
  const hrefs = [...esMatch[1].matchAll(/'(\/es\/caderno\/[a-z-]+)'/g)].map(m => m[1]);
  assert.strictEqual(hrefs.length, 3, `esperava 3 links de card ES, achou ${hrefs.length}`);
  const routeMap = new Map(routes);
  for (const href of hrefs) {
    assert.ok(routeMap.has(href), `card ES linka para rota desconhecida: ${href}`);
    const target = routeMap.get(href);
    assert.ok(existsSync(join(here, target)), `rota ${href} aponta para arquivo inexistente: ${target}`);
  }
});

test('nginx.runtime.conf declara as 6 rotas do Caderno (PT+ES) sem add_header próprio', () => {
  const conf = readFileSync(join(here, '..', 'nginx.runtime.conf'), 'utf8');
  for (const [route, target] of routes) {
    const escapedRoute = route.replace(/\//g, '\\/');
    const re = new RegExp(`location = ${escapedRoute} \\{ try_files \\/${target.replace(/\//g, '\\/')} =404; \\}`);
    assert.ok(re.test(conf), `rota ausente ou malformada no nginx.runtime.conf: ${route}`);
  }
});

let failed = 0;
for (const [name, fn] of tests) {
  try {
    fn();
    console.log(`ok - ${name}`);
  } catch (err) {
    failed++;
    console.error(`not ok - ${name}`);
    console.error(err.message);
  }
}
console.log(`\n${tests.length - failed}/${tests.length} passed`);
if (failed > 0) process.exit(1);
