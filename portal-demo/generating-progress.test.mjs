// Testes da experiência de espera durante geração de leitura (portal-demo/index.html).
//
// Cobre:
// - generateReading NÃO navega automaticamente (fica no dashboard)
// - card em inProgress exibe animação CSS e botão "Gerando leitura…"
// - clicar num card em inProgress abre a tela de progresso (usuário decide)
// - pollReadingUntilDone NÃO chama showReadingView quando usuário está no dashboard
// - showReadyNotification aparece no dashboard ao terminar
// - dismiss da notificação funciona
// - applyRemoteState retoma polling para in_progress sem poll ativo (aba reaberta)
// - renderGeneratingProgress exibe barra determinista quando sections_total > 0
// - renderGeneratingProgress exibe barra indeterminada quando sections_total == 0
// - duas abas: pollingContentIds impede duplo-poll para mesmo content_id
// - fallback (status='fallback') encerra poll igual a 'ready'
// - timeout de poll: item mantém inProgress, sem navegar
//
// Roda com: ``node portal-demo/generating-progress.test.mjs``.

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

// ── Estrutura HTML ────────────────────────────────────────────────────────────

test('container ready-notifications existe no DOM antes do library-grid', () => {
  const notifIdx = html.indexOf('id="ready-notifications"');
  const gridIdx = html.indexOf('id="library-grid"');
  assert.ok(notifIdx !== -1, 'div#ready-notifications ausente');
  assert.ok(notifIdx < gridIdx, 'ready-notifications deve vir antes de library-grid');
});

test('ready-notifications tem aria-live="polite" para acessibilidade', () => {
  assert.match(html, /id="ready-notifications"[^>]*aria-live="polite"/);
});

// ── CSS: animações ────────────────────────────────────────────────────────────

test('@keyframes gen-indeterminate definido (barra sem progresso conhecida)', () => {
  assert.match(html, /@keyframes gen-indeterminate/);
});

test('@keyframes asset-pulse definido (card pulsante durante geração)', () => {
  assert.match(html, /@keyframes asset-pulse/);
});

test('.asset[data-state="in_progress"] usa asset-pulse', () => {
  assert.match(html, /\.asset\[data-state="in_progress"\]\s*\{[^}]*animation:[^}]*asset-pulse/);
});

test('estilos de ready-notification usam variáveis CSS existentes (--gold-soft, --gold-line)', () => {
  assert.match(html, /\.ready-notification[^}]*var\(--gold-soft\)/);
  assert.match(html, /\.ready-notification[^}]*var\(--gold-line\)/);
});

// ── appState: campos novos ────────────────────────────────────────────────────

test('appState tem activeReadingId e pollingContentIds', () => {
  assert.match(script, /const appState = \{[^}]*activeReadingId:\s*null/);
  assert.match(script, /const appState = \{[^}]*pollingContentIds:\s*new Set\(\)/);
});

// ── generateReading: não navega sozinho ──────────────────────────────────────

test('generateReading não chama showReadingView() nem navigateToReading() diretamente ao iniciar geração', () => {
  const match = script.match(/async function generateReading\(item\)\s*\{[\s\S]*?^\s*\}/m);
  assert.ok(match, 'generateReading não encontrado');
  const body = match[0];
  // A função pode chamar showReadingProgress (que internamente chama showReadingView),
  // mas só para cache hit ou inProgress — não para nova geração (status 202).
  // Verificamos que o bloco de 202 não chama showReadingView diretamente.
  const inProgressBlock = body.match(/if \(data\.reading && data\.reading\.status === 'in_progress'\)\s*\{([\s\S]*?)\}/);
  assert.ok(inProgressBlock, 'bloco de in_progress não encontrado em generateReading');
  assert.ok(!inProgressBlock[1].includes('showReadingView'), 'showReadingView chamado no bloco 202 — não deveria');
  assert.ok(!inProgressBlock[1].includes('navigateToReading'), 'navigateToReading chamado no bloco 202 — não deveria');
});

test('generateReading chama pollReadingUntilDone sem await no bloco 202 (fire-and-forget)', () => {
  const match = script.match(/async function generateReading\(item\)\s*\{[\s\S]*?^\s*\}/m);
  assert.ok(match, 'generateReading não encontrado');
  const body = match[0];
  const inProgressBlock = body.match(/if \(data\.reading && data\.reading\.status === 'in_progress'\)\s*\{([\s\S]*?)\}/);
  assert.ok(inProgressBlock, 'bloco de in_progress não encontrado');
  // Deve chamar pollReadingUntilDone mas NÃO com await
  assert.match(inProgressBlock[1], /pollReadingUntilDone\(item\)/);
  assert.ok(!/await\s+pollReadingUntilDone/.test(inProgressBlock[1]), 'pollReadingUntilDone é awaited — deveria ser fire-and-forget');
});

test('generateReading retorna sem navegar quando já está gerando (pollingContentIds guard)', () => {
  assert.match(script, /appState\.pollingContentIds\.has\(item\.id\)[\s\S]*?showReadingProgress\(item\)/);
});

// ── pollReadingUntilDone: não sequestra ───────────────────────────────────────

test('pollReadingUntilDone tem guard de duplo-poll (pollingContentIds)', () => {
  assert.match(script, /async function pollReadingUntilDone\(item\)\s*\{[\s\S]*?appState\.pollingContentIds\.has\(item\.id\)/);
});

test('pollReadingUntilDone usa try/finally para limpar pollingContentIds', () => {
  // Verifica no script completo: add deve aparecer antes de finally e delete deve aparecer depois.
  const fnStart = script.indexOf('async function pollReadingUntilDone(item)');
  assert.ok(fnStart !== -1, 'pollReadingUntilDone não encontrado');
  const fnSlice = script.slice(fnStart, fnStart + 4000); // slice generoso
  assert.ok(fnSlice.includes('pollingContentIds.add(item.id)'), 'pollingContentIds.add ausente');
  assert.ok(fnSlice.includes('pollingContentIds.delete(item.id)'), 'pollingContentIds.delete ausente');
  assert.ok(fnSlice.includes('finally'), 'bloco finally ausente — pollingContentIds pode vazar');
  // Garante que delete vem depois de add no mesmo bloco de função.
  const addIdx = fnSlice.indexOf('pollingContentIds.add');
  const deleteIdx = fnSlice.indexOf('pollingContentIds.delete');
  assert.ok(deleteIdx > addIdx, 'pollingContentIds.delete deveria aparecer após .add');
});

test('pollReadingUntilDone chama showReading quando userIsWatching (activeReadingId + readingViewVisible)', () => {
  assert.match(script, /userIsWatching[\s\S]{0,200}showReading\(item, reading\)/);
});

test('pollReadingUntilDone chama showReadyNotification quando usuário não está assistindo', () => {
  assert.match(script, /showReadyNotification\(item\)/);
});

test('pollReadingUntilDone NÃO chama showReadingView() diretamente ao terminar', () => {
  const fnStart = script.indexOf('async function pollReadingUntilDone(item)');
  assert.ok(fnStart !== -1, 'pollReadingUntilDone não encontrado');
  const fnSlice = script.slice(fnStart, fnStart + 4000);
  // showReading internamente chama showReadingView — isso é esperado.
  // O que não pode existir é uma chamada DIRETA a showReadingView() dentro de pollReadingUntilDone.
  // Como showReading chama showReadingView internamente, verificamos que não há chamada standalone.
  const directCall = fnSlice.match(/(?<!showReading\(item, reading\)[\s\S]{0,0})showReadingView\(\)/);
  // Alternativa mais simples: a função não deve conter a string showReadingView() em nenhum ponto
  // fora de um call para showReading — como não podemos parsear JS no teste, verificamos que
  // showReadingView() não aparece em nenhum lugar da função (showReading usa internamente mas
  // não como string visível no bloco de pollReadingUntilDone).
  assert.ok(!fnSlice.includes('showReadingView()'), 'showReadingView() chamado diretamente em pollReadingUntilDone — não deveria');
});

test('pollReadingUntilDone trata status fallback como conclusão (não fica em loop)', () => {
  // O guard `reading.status === 'in_progress'` continua; qualquer outro status (incluindo fallback) encerra.
  assert.match(script, /reading\.status === 'in_progress'\) continue/);
});

test('pollReadingUntilDone timeout não chama showLibraryView (usuário não é redirecionado)', () => {
  const match = script.match(/async function pollReadingUntilDone\(item\)\s*\{[\s\S]*?^\s*\}/m);
  assert.ok(match, 'pollReadingUntilDone não encontrado');
  const body = match[0];
  // Após o loop de tentativas (timeout): deve mostrar toast mas NÃO mudar de tela.
  const afterLoop = body.slice(body.lastIndexOf('for (let attempt'));
  // Encontra o trecho após o fechamento do for
  const afterFor = afterLoop.slice(afterLoop.indexOf('\n      }') + 8);
  assert.ok(!afterFor.includes('showLibraryView()'), 'showLibraryView() chamado no timeout — não deveria');
  assert.ok(!afterFor.includes('showReadingView()'), 'showReadingView() chamado no timeout — não deveria');
});

// ── openReading: inProgress abre tela de progresso ───────────────────────────

test('openReading com item.state=inProgress chama showReadingProgress em vez de generateReading', () => {
  assert.match(script, /item\.state === ACCESS_STATES\.inProgress\)\s*\{\s*showReadingProgress\(item\);\s*return;\s*\}/);
});

// ── showReadingProgress: nova função ─────────────────────────────────────────

test('showReadingProgress existe e define appState.activeReadingId', () => {
  assert.match(script, /function showReadingProgress\(item\)/);
  assert.match(script, /appState\.activeReadingId = item\.id/);
});

test('showReadingProgress navega para a tela de leitura (é escolha do usuário)', () => {
  const match = script.match(/function showReadingProgress\(item\)\s*\{[\s\S]*?\n    \}/);
  assert.ok(match, 'showReadingProgress não encontrado');
  assert.ok(match[0].includes('navigateToReading'), 'showReadingProgress não chama navigateToReading');
  assert.ok(match[0].includes('showReadingView'), 'showReadingProgress não chama showReadingView');
});

// ── showLibraryView: limpa activeReadingId ────────────────────────────────────

test('showLibraryView limpa appState.activeReadingId ao voltar ao dashboard', () => {
  assert.match(script, /function showLibraryView\(\)\s*\{[\s\S]*?appState\.activeReadingId = null/);
});

// ── renderGeneratingProgress: barra determinista / indeterminada ──────────────

test('renderGeneratingProgress existe', () => {
  assert.match(script, /function renderGeneratingProgress\(reading\)/);
});

test('renderGeneratingProgress usa gen-bar com porcentagem quando sections_total > 0', () => {
  // Template string tem gen-bar como class e role="progressbar" no mesmo elemento.
  assert.match(script, /class="gen-bar".*role="progressbar"/);
  assert.match(script, /width:\$\{pct\}%/);
});

test('renderGeneratingProgress usa gen-bar-indeterminate quando não há progresso conhecido', () => {
  assert.match(script, /gen-bar-indeterminate/);
});

// ── showReadyNotification ─────────────────────────────────────────────────────

test('showReadyNotification existe e cria elemento com data-ready-for', () => {
  assert.match(script, /function showReadyNotification\(item\)/);
  assert.match(script, /div\.dataset\.readyFor = item\.id/);
});

test('showReadyNotification adiciona botão data-dismiss-notification', () => {
  assert.match(script, /data-dismiss-notification/);
});

test('showReadyNotification tem auto-dismiss (setTimeout)', () => {
  const match = script.match(/function showReadyNotification\(item\)\s*\{[\s\S]*?\n    \}/);
  assert.ok(match, 'showReadyNotification não encontrado');
  assert.ok(match[0].includes('setTimeout'), 'auto-dismiss ausente em showReadyNotification');
});

// ── Event handler: dismiss ────────────────────────────────────────────────────

test('event handler processa data-dismiss-notification e remove o elemento', () => {
  assert.match(script, /target\.matches\('\[data-dismiss-notification\]'\)/);
  assert.match(script, /data-ready-for.*remove\(\)/);
});

// ── applyRemoteState: retoma polling para in_progress órfão ──────────────────

test('applyRemoteState retoma pollReadingUntilDone para itens in_progress sem poll ativo', () => {
  const match = script.match(/function applyRemoteState\(\)\s*\{[\s\S]*?\n    \}/);
  assert.ok(match, 'applyRemoteState não encontrado');
  const body = match[0];
  assert.ok(body.includes('pollReadingUntilDone'), 'applyRemoteState não retoma polling');
  assert.ok(body.includes('pollingContentIds.has'), 'applyRemoteState não verifica poll ativo antes de retomar');
});

// ── Sem framework/CDN novo ────────────────────────────────────────────────────

test('nenhuma tag <link>/<script> externa nova foi adicionada', () => {
  const externalTags = html.match(/<(?:link|script)[^>]+(?:href|src)="https?:\/\/[^"]+"/g) || [];
  const allowed = externalTags.filter((tag) => tag.includes('fonts.googleapis.com') || tag.includes('fonts.gstatic.com'));
  assert.equal(externalTags.length, allowed.length, `origem externa nova: ${JSON.stringify(externalTags.filter((t) => !allowed.includes(t)))}`);
});

// ── Runner ────────────────────────────────────────────────────────────────────

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
