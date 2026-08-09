import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const pixel = await readFile(new URL('./meta-pixel.js', import.meta.url), 'utf8');

for (const [pathname, expectedLocale] of [
  ['/', 'pt-BR'],
  ['/es', 'es-AR'],
  ['/es/diario-astral-1', 'es-AR'],
]) {
  const document = {
    createElement: () => ({}),
    getElementsByTagName: () => [{ parentNode: { insertBefore: () => {} } }],
  };
  const window = { location: { pathname } };
  vm.runInNewContext(pixel, { window, document });

  assert.equal(window.fbq.queue[0][0], 'init');
  assert.equal(window.fbq.queue[0][1], '2812114902493745');
  assert.equal(window.fbq.queue[1][1], 'PageView');
  assert.equal(window.fbq.queue[1][2].content_language, expectedLocale);
}

const checkout = await readFile(new URL('./checkout.html', import.meta.url), 'utf8');
const thankYou = await readFile(new URL('./obrigado.html', import.meta.url), 'utf8');
const lpV1 = await readFile(new URL('../lp-diario-astral/v1/index.html', import.meta.url), 'utf8');
const lpV2 = await readFile(new URL('../lp-diario-astral/v2/index.html', import.meta.url), 'utf8');

assert.match(lpV1, /fbq\('track','ViewContent'/);
assert.match(lpV2, /fbq\('track','ViewContent'/);
assert.match(lpV1, /fbq\('track',\s*'InitiateCheckout'/);
assert.match(lpV2, /fbq\('track',\s*'InitiateCheckout'/);
assert.match(checkout, /fbq\('track', 'InitiateCheckout'/);
assert.match(thankYou, /fbq\('track', 'Purchase'/);
assert.match(thankYou, /\/api\/checkout\/order\/\$\{encodeURIComponent\(orderId\)\}\/conversion/);
assert.match(thankYou, /\{ eventID: conversion\.event_id \}/);
assert.ok(
  thankYou.indexOf('/conversion`') < thankYou.indexOf("fbq('track', 'Purchase'"),
  'Purchase must only fire after the server confirms the order',
);

console.log('meta pixel manual tracking: ok');
