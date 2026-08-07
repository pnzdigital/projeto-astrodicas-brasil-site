// Testes da página dedicada de leitura (portal-demo/index.html).
//
// Cobre: card da biblioteca navega para a página do mapa (sem acordeão/modal),
// página renderiza seções com hierarquia título/subtítulo/corpo, índice
// navegável com um item por seção, estado sobrevive ao F5 via hash
// (#/leitura/<content_id>), caminho de volta para a lista, e o aviso de
// hora assumida aparece só quando birth_time_assumed é true.
//
// Roda com: ``node portal-demo/reading-page.test.mjs``.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, 'index.html'), 'utf8');
const scriptMatch = html.match(/<script type="module">([\s\S]*?)<\/script>\s*<\/body>/);
const script = scriptMatch ? scriptMatch[1] : '';

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

// ── Navegação: clicar no card do mapa abre a página, não modal/acordeão ────

test('reading-dialog (modal) foi removido — leitura não abre mais em modal apertado', () => {
  assert.ok(!/id="reading-dialog"/.test(html), 'dialog#reading-dialog ainda existe');
});

test('existe uma página dedicada de leitura fora do fluxo de dashboard', () => {
  assert.match(html, /<div class="page reading-page" id="reading-view" hidden>/);
});

test('clique no card ([data-read]) chama openReading, que leva à página de leitura via showReading/navigateToReading', () => {
  assert.match(script, /target\.matches\('\[data-read\]'\)\)\s*openReading\(/);
  assert.match(script, /function openReading\(item\)/);
  assert.match(script, /function showReading\(item, reading\)\s*\{[\s\S]*?navigateToReading\(reading\.content_id \|\| item\.id\);[\s\S]*?showReadingView\(\);/);
});

test('dashboard e página de leitura se alternam por hidden, nunca dois visíveis juntos', () => {
  assert.match(script, /function showReadingView\(\)\s*\{[\s\S]*?dashboard-view'\)\.hidden = true;[\s\S]*?reading-view'\)\.hidden = false;/);
  assert.match(script, /function showLibraryView\(\)\s*\{[\s\S]*?reading-view'\)\.hidden = true;[\s\S]*?dashboard-view'\)\.hidden = false;/);
});

// ── Conteúdo da página: título, dados de nascimento, hierarquia de seções ──

test('página mostra título, kicker e bloco de dados de nascimento', () => {
  assert.match(html, /id="reading-page-title"/);
  assert.match(html, /id="reading-page-kicker"/);
  assert.match(html, /id="reading-page-birth"/);
  assert.match(script, /function renderReadingBirth\(\)/);
  assert.match(script, /profile\.birth_date, profile\.birth_time, profile\.birth_city/);
});

test('seções são renderizadas com hierarquia título (h2) / subtítulo / corpo, uma <section> por item', () => {
  assert.match(script, /function buildReadingSections\(reading, item\)/);
  assert.match(script, /body \+= `<section class="reading-section" id="\$\{sectionId\}"><h2>\$\{escapeHtml\(section\.title \|\| ''\)\}<\/h2>`;/);
  assert.match(script, /if \(section\.subtitle\) body \+= `<p class="reading-subtitle">/);
});

test('paridade com PDF: quando não há seções estruturadas, cai para body_html/reading estático (mesmo conteúdo do PDF)', () => {
  assert.match(script, /return \{ warning, toc: '', body: reading\.body_html \|\| item\.reading \|\| '' \};/);
});

// ── Índice navegável: um item por seção ─────────────────────────────────────

test('índice (toc) gera um link <a href="#reading-section-N"> por seção, mesmo id usado no <section>', () => {
  assert.match(script, /const sectionId = `reading-section-\$\{index\}`;/);
  assert.match(script, /toc \+= `<a href="#\$\{sectionId\}">\$\{escapeHtml\(section\.title \|\| ''\)\}<\/a>`;/);
  assert.match(html, /<nav class="reading-toc" id="reading-page-toc" aria-label="Índice da leitura" hidden><\/nav>/);
});

test('índice fica oculto quando não há seções (tocNode.hidden = !toc)', () => {
  assert.match(script, /tocNode\.hidden = !toc;/);
});

// ── Botão de PDF em destaque ─────────────────────────────────────────────

test('botão de baixar PDF completo existe na página, com href para /api/me/readings/<id>/pdf', () => {
  assert.match(html, /id="reading-page-pdf" href="#" download hidden>Baixar PDF completo</);
  assert.match(script, /pdfLink\.href = `\$\{API_BASE\}\/api\/me\/readings\/\$\{encodeURIComponent\(reading\.content_id\)\}\/pdf`;/);
});

// ── Aviso de hora assumida: aparece só quando birth_time_assumed é true ────

test('renderAscendantWarning só retorna markup quando birth_time_assumed E ascendant_warning estão presentes', () => {
  assert.match(script, /function renderAscendantWarning\(reading\)\s*\{[\s\S]*?if \(!reading\.birth_time_assumed \|\| !reading\.ascendant_warning\) return '';/);
});

// ── Estado linkável: hash sobrevive ao F5 ───────────────────────────────────

test('rota usa hash no formato #/leitura/<content_id>, sem colidir com âncoras existentes (#inicio, #entregaveis, etc.)', () => {
  assert.match(script, /function navigateToReading\(contentId\)\s*\{[\s\S]*?const target = `#\/leitura\/\$\{encodeURIComponent\(contentId\)\}`;/);
  assert.match(script, /function readingIdFromHash\(\)\s*\{[\s\S]*?location\.hash\.match\(\/\^#\\\/leitura\\\/\(\.\+\)\$\/\);/);
});

test('handleHashRoute reabre a leitura pelo content_id do hash ao carregar/recarregar a página', () => {
  assert.match(script, /function handleHashRoute\(\)\s*\{[\s\S]*?const contentId = readingIdFromHash\(\);[\s\S]*?if \(contentId\) openReadingById\(contentId\);/);
  assert.match(script, /function openReadingById\(contentId\)\s*\{[\s\S]*?generateReading\(item\);/);
});

test('handleHashRoute é chamado após o bootstrap resolver (dado carregado antes de tentar abrir o hash)', () => {
  const bootstrapMatch = script.match(/async function bootstrap\(\)\s*\{[\s\S]*?\n    \}/);
  assert.ok(bootstrapMatch, 'função bootstrap não encontrada');
  assert.ok(bootstrapMatch[0].includes('handleHashRoute();'), 'bootstrap não chama handleHashRoute() ao final');
});

test('hashchange e popstate re-executam handleHashRoute (navegação/F5 refletidos)', () => {
  assert.match(script, /window\.addEventListener\('hashchange', handleHashRoute\);/);
  assert.match(script, /window\.addEventListener\('popstate', handleHashRoute\);/);
});

// ── Caminho de volta para a lista ───────────────────────────────────────────

test('existem dois links de volta para a biblioteca (topo e rodapé da página de leitura)', () => {
  const matches = html.match(/<a[^>]+data-back-to-library/g) || [];
  assert.equal(matches.length, 2, 'esperava 2 ocorrências de data-back-to-library (topo + rodapé)');
});

test('clicar em voltar navega para #entregaveis e chama showLibraryView', () => {
  assert.match(script, /target\.matches\('\[data-back-to-library\]'\)\)\s*\{\s*event\.preventDefault\(\);\s*history\.pushState\(null, '', location\.pathname \+ location\.search \+ '#entregaveis'\);\s*showLibraryView\(\);/);
});

// ── pt-BR e es-AR: mecanismo de localização já existente ───────────────────

test('strings novas da página de leitura entram no dicionário de tradução es-AR (applySpanishPortalCopy)', () => {
  const match = script.match(/function applySpanishPortalCopy\(\)\s*\{[\s\S]*?\n    \}/);
  assert.ok(match, 'função applySpanishPortalCopy não encontrada');
  const body = match[0];
  assert.ok(body.includes("'Voltar para minha biblioteca': 'Volver a mi biblioteca'"), 'tradução do link de voltar ausente');
  assert.ok(body.includes("'Baixar PDF completo': 'Descargar PDF completo'"), 'tradução do botão de PDF ausente');
});

test('kicker da leitura usa IS_ARGENTINA em vez de string fixa (evita vazamento de idioma no conteúdo dinâmico)', () => {
  assert.match(script, /IS_ARGENTINA \? 'lectura web' : 'leitura web'/);
  assert.match(script, /IS_ARGENTINA \? 'generación en curso' : 'geração em andamento'/);
});

test('dados de nascimento no topo usam rótulo localizado (pt/es)', () => {
  assert.match(script, /IS_ARGENTINA \? 'Datos de nacimiento' : 'Dados de nascimento'/);
});

// ── Sem framework/CDN novo: só reaproveita CSS/JS existente do portal ──────

test('CSS da página de leitura reaproveita variáveis existentes (--gold, --cream, --display), sem cores hardcoded novas fora da paleta', () => {
  const cssMatch = html.match(/\.reading-header \{[\s\S]*?\.reading-body p \{[^}]*\}/);
  assert.ok(cssMatch, 'bloco de CSS da página de leitura não encontrado');
  assert.ok(cssMatch[0].includes('var(--gold'), 'CSS novo não usa as variáveis de cor existentes');
});

test('nenhuma tag <link>/<script> externa nova foi adicionada (CSP de produção bloqueia origem não declarada)', () => {
  const externalTags = html.match(/<(?:link|script)[^>]+(?:href|src)="https?:\/\/[^"]+"/g) || [];
  const allowed = externalTags.filter((tag) => tag.includes('fonts.googleapis.com') || tag.includes('fonts.gstatic.com'));
  assert.equal(externalTags.length, allowed.length, `origem externa nova encontrada: ${JSON.stringify(externalTags.filter((t) => !allowed.includes(t)))}`);
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
