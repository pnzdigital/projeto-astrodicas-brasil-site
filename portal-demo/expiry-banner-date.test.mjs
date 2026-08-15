import { test } from 'node:test';
import { strict as assert } from 'node:assert';

// Reproduz a lógica de rótulo hoje/amanhã de renderExpiryBanner.
function expiryDayLabel(isoDate, isArgentina = false) {
  const end = new Date(isoDate);
  const now = new Date();
  const tomorrow = new Date(now);
  tomorrow.setDate(now.getDate() + 1);
  const isToday = end.toDateString() === now.toDateString();
  const isTomorrow = !isToday && end.toDateString() === tomorrow.toDateString();
  if (isArgentina) return isToday ? 'hoy' : isTomorrow ? 'mañana' : 'outro';
  return isToday ? 'hoje' : isTomorrow ? 'amanhã' : 'outro';
}

// Trial block: janela de 36h — testa que o rótulo é correto dentro dela.
function msLeft(isoDate) {
  return new Date(isoDate).getTime() - Date.now();
}

function inWindow(isoDate) {
  const ms = msLeft(isoDate);
  return ms >= 0 && ms <= 36 * 3600 * 1000;
}

test('trial vence hoje às 23:59 → label "hoje" (PT)', () => {
  const d = new Date(); d.setHours(23, 59, 0, 0);
  assert(inWindow(d.toISOString()), 'deve estar na janela de 36h');
  assert.equal(expiryDayLabel(d.toISOString()), 'hoje');
});

test('trial vence hoje às 23:59 → label "hoy" (ES)', () => {
  const d = new Date(); d.setHours(23, 59, 0, 0);
  assert.equal(expiryDayLabel(d.toISOString(), true), 'hoy');
});

test('trial vence amanhã às 10:00 → label "amanhã" (PT)', () => {
  const d = new Date(); d.setDate(d.getDate() + 1); d.setHours(10, 0, 0, 0);
  assert(inWindow(d.toISOString()), 'deve estar na janela de 36h');
  assert.equal(expiryDayLabel(d.toISOString()), 'amanhã');
});

test('trial vence amanhã às 10:00 → label "mañana" (ES)', () => {
  const d = new Date(); d.setDate(d.getDate() + 1); d.setHours(10, 0, 0, 0);
  assert.equal(expiryDayLabel(d.toISOString(), true), 'mañana');
});

test('trial vence em 9h (hoje) → label "hoje", NÃO "amanhã"', () => {
  const d = new Date(Date.now() + 9 * 3600 * 1000);
  assert(inWindow(d.toISOString()), 'deve estar na janela de 36h');
  const label = expiryDayLabel(d.toISOString());
  assert.equal(label, 'hoje', `esperado "hoje", obtido "${label}"`);
});

test('trial vence além de 36h → fora da janela', () => {
  const d = new Date(Date.now() + 40 * 3600 * 1000);
  assert.equal(inWindow(d.toISOString()), false);
});

// Entitlement block: days_remaining 0 = hoje, 1 = amanhã
function entDayLabel(daysRemaining, expiresAt, isArgentina = false) {
  let isToday = daysRemaining === 0;
  let isTomorrow = daysRemaining === 1;
  if (expiresAt) {
    const exp = new Date(expiresAt); const now = new Date();
    const tom = new Date(now); tom.setDate(now.getDate() + 1);
    isToday = exp.toDateString() === now.toDateString();
    isTomorrow = !isToday && exp.toDateString() === tom.toDateString();
  }
  if (isArgentina) return isToday ? 'hoy' : isTomorrow ? 'mañana' : 'outro';
  return isToday ? 'hoje' : isTomorrow ? 'amanhã' : 'outro';
}

test('days_remaining=0 → label "hoje"', () => {
  assert.equal(entDayLabel(0, null), 'hoje');
});

test('days_remaining=1 → label "amanhã"', () => {
  assert.equal(entDayLabel(1, null), 'amanhã');
});

test('days_remaining=0 com expires_at hoje → label "hoje"', () => {
  const d = new Date(); d.setHours(23, 59, 0, 0);
  assert.equal(entDayLabel(0, d.toISOString()), 'hoje');
});

test('days_remaining=1 com expires_at amanhã → label "amanhã"', () => {
  const d = new Date(); d.setDate(d.getDate() + 1); d.setHours(10, 0, 0, 0);
  assert.equal(entDayLabel(1, d.toISOString()), 'amanhã');
});

test('days_remaining=1 com expires_at amanhã → label "mañana" (ES)', () => {
  const d = new Date(); d.setDate(d.getDate() + 1); d.setHours(10, 0, 0, 0);
  assert.equal(entDayLabel(1, d.toISOString(), true), 'mañana');
});
