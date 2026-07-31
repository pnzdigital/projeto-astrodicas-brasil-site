import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

const html = await readFile(new URL('./index.html', import.meta.url), 'utf8');
const argentina = await readFile(new URL('./portal-config-ar.js', import.meta.url), 'utf8');

for (const marker of [
  '/api/session',
  '/api/auth/${mode}',
  '/api/me/profile',
  '/api/me/access',
  '/api/me/readings',
  '/api/me/readings/${encodeURIComponent(item.id)}/generate',
  'id="profile-form"',
  'data-auth-form="login"',
  'data-auth-form="register"',
  'DEMO_PAID',
]) assert.ok(html.includes(marker), `missing portal integration marker: ${marker}`);

assert.match(argentina, /defaultProvider:\s*"mercado_pago"/);
assert.match(argentina, /locale:\s*"es-AR"/);
assert.match(html, /window\.location\.pathname\.startsWith\('\/es\/'\)/);
assert.equal((html.match(/<script type="module">/g) || []).length, 1);
assert.equal((html.match(/<\/script>/g) || []).length, 1);
console.log('portal smoke checks: ok');
